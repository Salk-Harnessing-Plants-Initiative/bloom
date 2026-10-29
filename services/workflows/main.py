"""
Bloom Workflows API

HTTP endpoints for Bloom workflow tasks.

Run:
    uvicorn main:app --host 0.0.0.0 --port 5100 --reload

Endpoints:
    GET  /health                                     - liveness (internal-only)
    POST /cyl/experiments/{experiment_id}/scans/{scan_id}/video
                                                     - queue a render of the cyl
                                                       scan's video for
                                                       cyl_video_worker.py; answers
                                                       202 with the job
                                                       (requires a Supabase user JWT)
    POST /gravi/experiments/{experiment_id}/plate-video
                                                     - queue a render of one plate's
                                                       time-lapse for one wave for
                                                       plate_video_worker.py; answers
                                                       202 with the job
                                                       (requires a Supabase user JWT)
    POST /pipeline                                  - externally reachable as
                                                       POST /workflows/pipeline
                                                       (Caddy strips the /workflows
                                                       prefix before proxying here,
                                                       matching every route above):
                                                       trigger an A4 sleap-roots
                                                       pipeline run for a scan/wave/
                                                       experiment/explicit scan list
                                                       (requires a Supabase user JWT)
    GET  /model-cards                               - externally reachable as
                                                       GET /workflows/model-cards:
                                                       the production model cards from
                                                       the wandb registry, cached 300 s
                                                       and warmed at startup, for the
                                                       pipeline confirm dialog's model
                                                       warnings (requires a Supabase
                                                       user JWT; its own rate limit)
    GET  /runs/{run_id}                             - externally reachable as
                                                       GET /workflows/runs/{run_id}:
                                                       read a pipeline run's current
                                                       status + its scans, exactly as
                                                       stored — does NOT itself query
                                                       Argo/K8s; live reconciliation is
                                                       exclusively status_poller.py's job
                                                       (requires a Supabase user JWT)
    POST /scrna/cellranger/runs                     - start a Cell Ranger run for one
                                                       sample + a reference: writes
                                                       the run and queues it for the
                                                       dispatch worker
                                                       (requires a Supabase user JWT)
    GET  /scrna/cellranger/runs/{run_id}            - a Cell Ranger run as stored
                                                       (requires a Supabase user JWT)
    GET  /scrna/cellranger/runs/{run_id}/logs       - the end of one step's log
         ?step=<step>                                  (requires a Supabase user JWT)
"""

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from postgrest import APIError

import model_cards
import pipeline
import plate_request
import scrna_cellranger
import scrna_cellranger_logs
from auth import (
    enforce_folder_check_limit,
    enforce_model_cards_limit,
    enforce_rate_limit,
    require_supabase_user,
)
from supabase_client import SINGLE_ROW_RPC_TIMEOUT_SECONDS, app_client
from video import scan_in_experiment
from video_queue import CYL_QUEUE, enqueue

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Comma-separated browser origins allowed to call this API (the frontend).
# CORS only restricts browser JS — it is not access control for curl/servers.
CORS_ORIGINS = os.environ.get("WORKFLOWS_CORS_ORIGINS", "http://localhost:3000").split(
    ","
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the model-card cache in the background so the first confirm dialog
    # after a deploy doesn't wait on wandb; startup and /health never wait for it.
    # model_cards.warm() skips without a key and never raises.
    threading.Thread(
        target=model_cards.warm, name="model-cards-warm", daemon=True
    ).start()
    yield


app = FastAPI(title="Bloom Workflows API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# Liveness only — minimal by design. Kept for the in-container/orchestrator
# probe (http://localhost:5100/health); NOT exposed through the public proxy.
@app.get("/health")
def health():
    return {"status": "ok"}


# The database's own error names the role and PostgREST's codes, so callers get this.
VIDEO_NOT_QUEUED = "The video could not be queued right now. Try again shortly."


@app.post("/cyl/experiments/{experiment_id}/scans/{scan_id}/video", status_code=202)
def cyl_experiment_scan_video(
    experiment_id: int,
    scan_id: int,
    user_id: str = Depends(require_supabase_user),
):
    """Queue a render of a cyl scan's video, or join the one already queued.

    Requires a valid Supabase user JWT (Bearer). Rate-limited per user.
    """
    enforce_rate_limit(user_id)
    try:
        client = app_client(timeout_seconds=SINGLE_ROW_RPC_TIMEOUT_SECONDS)
        belongs = scan_in_experiment(client, experiment_id, scan_id)
    except Exception as exc:
        logger.error("scan video for scan %s could not be checked: %r", scan_id, exc)
        raise HTTPException(status_code=503, detail=VIDEO_NOT_QUEUED) from exc
    if not belongs:
        raise HTTPException(
            status_code=404,
            detail=f"Scan {scan_id} not found in experiment {experiment_id}",
        )

    try:
        job = enqueue(
            client,
            CYL_QUEUE,
            {
                "p_scan_id": scan_id,
                "p_experiment_id": experiment_id,
                "p_requested_by": user_id,
            },
        )
    except APIError as exc:
        # foreign_key_violation: the scan is not in this experiment, or does not exist.
        if exc.code == "23503":
            raise HTTPException(
                status_code=404,
                detail=f"Scan {scan_id} not found in experiment {experiment_id}",
            ) from exc
        # check_violation: the scan's active job is filed under another experiment.
        if exc.code == "23514":
            raise HTTPException(
                status_code=409,
                detail=f"Scan {scan_id} is already being rendered under another experiment",
            ) from exc
        logger.error("scan video for scan %s could not be queued: %r", scan_id, exc)
        raise HTTPException(status_code=503, detail=VIDEO_NOT_QUEUED) from exc
    except Exception as exc:
        logger.error("scan video for scan %s could not be queued: %r", scan_id, exc)
        raise HTTPException(status_code=503, detail=VIDEO_NOT_QUEUED) from exc

    logger.info(
        "scan video for experiment %s scan %s requested by %s: job %s (%s)",
        experiment_id,
        scan_id,
        user_id,
        job["job_id"],
        "queued" if job["created"] else "already queued",
    )
    return job


@app.post("/gravi/experiments/{experiment_id}/plate-video", status_code=202)
def gravi_plate_video(
    experiment_id: int,
    body: dict,
    user_id: str = Depends(require_supabase_user),
):
    """Queue a render of one plate's time-lapse for one wave, or join the one
    already queued.

    `plate_id` and `wave_number` travel in the body, not the path — `plate_id`
    is free text, and the cylinder route's integer-only path defence does not
    transfer to it.

    Requires a valid Supabase user JWT (Bearer). Rate-limited per user.
    """
    # Read before anything can refuse, so a rejected body is still recorded as
    # a request for something. `%r` below because neither has been validated on
    # that path, and an unescaped newline in a plate id would forge a log line.
    asked = body if isinstance(body, dict) else {}

    try:
        enforce_rate_limit(user_id)
        job = plate_request.enqueue(experiment_id, body, user_id)
    except HTTPException as exc:
        logger.info(
            "plate video for experiment %s plate %r wave %r requested by %s: %s refused — %r",
            experiment_id,
            asked.get("plate_id"),
            asked.get("wave_number"),
            user_id,
            exc.status_code,
            exc.detail,
        )
        raise

    logger.info(
        "plate video for experiment %s plate %r wave %r requested by %s: job %s (%s)",
        experiment_id,
        asked.get("plate_id"),
        asked.get("wave_number"),
        user_id,
        job["job_id"],
        "queued" if job["created"] else "already queued",
    )
    return job


@app.post("/pipeline")
def trigger_pipeline_route(
    body: dict,
    user_id: str = Depends(require_supabase_user),
):
    """Trigger an A4 pipeline run (reachable externally at POST /workflows/pipeline
    — Caddy's handle_path /workflows/* already strips that prefix before proxying
    to this service, so this route is registered without it, matching every other
    route above).

    Requires a valid Supabase user JWT (Bearer). Rate-limited per user.
    """
    enforce_rate_limit(user_id)
    result = pipeline.trigger_pipeline(body, user_id)
    logger.info(
        "Pipeline run %s triggered by %s (%d scans, %d reused)",
        result["pipeline_run_id"],
        user_id,
        result["scan_count"],
        result["reused_count"],
    )
    return result


@app.get("/model-cards")
def model_cards_route(user_id: str = Depends(require_supabase_user)):
    """The production model cards from the wandb registry (reachable externally
    at GET /workflows/model-cards), for the pipeline confirm dialog's
    past-window and no-model warnings (bloom#971).

    Requires a valid Supabase user JWT (Bearer). It has its own per-user
    limit (MODEL_CARDS_RATE_LIMIT, scope "model-cards") instead of the shared
    5-per-60s one, which dialog opens would use up. The cache, background
    refresh and backoff in model_cards bound the cost upstream. A sync def, so
    a cold request's wait for a refresh happens in the threadpool.
    """
    enforce_model_cards_limit(user_id)
    try:
        cards, fetched_at, skipped = model_cards.list_production_cards()
    except model_cards.ModelCatalogNotConfigured:
        raise HTTPException(
            status_code=503,
            detail="The model catalog isn't configured in this environment.",
        )
    except model_cards.ModelCatalogUnavailable:
        # Already logged by model_cards; the cause never reaches the caller.
        raise HTTPException(status_code=503, detail="Couldn't read the model catalog.")
    except Exception:
        logger.exception("GET /model-cards failed")
        raise HTTPException(status_code=503, detail="Couldn't read the model catalog.")
    return {"cards": cards, "fetched_at": fetched_at, "skipped": skipped}


@app.get("/runs/{run_id}")
def get_pipeline_run_route(
    run_id: int,
    user_id: str = Depends(require_supabase_user),
):
    """Read a pipeline run's current status + its scans (reachable externally
    at GET /workflows/runs/{run_id}, same prefix-stripping as every other
    route above). A plain DB read — does NOT itself query Argo/K8s; live
    reconciliation is exclusively status_poller.py's job.

    Requires a valid Supabase user JWT (Bearer). Rate-limited per user.
    """
    enforce_rate_limit(user_id)
    return pipeline.get_run(run_id)


@app.post("/scrna/cellranger/runs", status_code=201)
def trigger_scrna_cellranger_run_route(
    body: dict,
    user_id: str = Depends(require_supabase_user),
):
    """Start a Cell Ranger run for one sample and a reference, and queue it.

    Requires a valid Supabase user JWT (Bearer). Rate-limited per user.
    """
    enforce_rate_limit(user_id)
    result = scrna_cellranger.trigger_run(body, user_id)
    logger.info(
        "Cell Ranger run %s triggered by %s (sample %s, reference %s, folder %s)",
        result["run_id"],
        user_id,
        result["sample"],
        result["reference"],
        result.get("fastq_url"),
    )
    return result


@app.post("/scrna/cellranger/folder-check")
def check_scrna_cellranger_folder_route(
    body: dict,
    user_id: str = Depends(require_supabase_user),
):
    """Check an S3 folder of FASTQs for a Cell Ranger run, without starting one: its
    sample, lanes, files, file count and total bytes, or a 422 saying what's wrong.

    Requires a valid Supabase user JWT (Bearer). Rate-limited per user, separately from
    starting runs, since the form checks as the scientist types.
    """
    enforce_folder_check_limit(user_id)
    return scrna_cellranger.check_folder(body)


@app.get("/scrna/cellranger/runs/{run_id}")
def get_scrna_cellranger_run_route(
    run_id: int,
    user_id: str = Depends(require_supabase_user),
):
    """A Cell Ranger run's row.

    Requires a valid Supabase user JWT (Bearer). Rate-limited per user.
    """
    enforce_rate_limit(user_id)
    return scrna_cellranger.get_run(run_id)


@app.get("/scrna/cellranger/runs/{run_id}/logs")
def get_scrna_cellranger_step_log_route(
    run_id: int,
    step: str,
    user_id: str = Depends(require_supabase_user),
):
    """The end of one step's log for a Cell Ranger run.

    Requires a valid Supabase user JWT (Bearer). Rate-limited per user.
    """
    enforce_rate_limit(user_id)
    return scrna_cellranger_logs.read_step_log(run_id, step)
