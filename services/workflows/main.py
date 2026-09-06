"""
Bloom Workflows API

HTTP endpoints for Bloom workflow tasks.

Run:
    uvicorn main:app --host 0.0.0.0 --port 5100 --reload

Endpoints:
    GET  /health                                     - liveness (internal-only)
    POST /cyl/experiments/{experiment_id}/scans/{scan_id}/video
                                                     - on-demand: generate the cyl
                                                       scan's video, upload it to
                                                       Storage, return a signed
                                                       download URL
                                                       (requires a Supabase user JWT)
    POST /gravi/experiments/{experiment_id}/plate-video
                                                     - on-demand: render one plate's
                                                       time-lapse for one wave, store
                                                       it and record it
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
    GET  /runs/{run_id}                             - externally reachable as
                                                       GET /workflows/runs/{run_id}:
                                                       read a pipeline run's current
                                                       status + its scans, exactly as
                                                       stored — does NOT itself query
                                                       Argo/K8s; live reconciliation is
                                                       exclusively status_poller.py's job
                                                       (requires a Supabase user JWT)
"""

import logging
import os

import pipeline
import plate_request
from auth import enforce_rate_limit, require_supabase_user
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from video import generate_experiment_scan_video

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Comma-separated browser origins allowed to call this API (the frontend).
# CORS only restricts browser JS — it is not access control for curl/servers.
CORS_ORIGINS = os.environ.get("WORKFLOWS_CORS_ORIGINS", "http://localhost:3000").split(
    ","
)

app = FastAPI(title="Bloom Workflows API", version="0.1.0")
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


@app.post("/cyl/experiments/{experiment_id}/scans/{scan_id}/video")
def cyl_experiment_scan_video(
    experiment_id: int,
    scan_id: int,
    user_id: str = Depends(require_supabase_user),
):
    """On-demand: generate a cyl scan's video (validated against the experiment).

    Requires a valid Supabase user JWT (Bearer). Rate-limited per user.
    """
    enforce_rate_limit(user_id)
    result = generate_experiment_scan_video(experiment_id, scan_id)
    # Split, because most requests for a scan that already has a video generate nothing. Saying
    # "Generated" either way, with a count describing the scan rather than the stored file,
    # would make the operator log assert work that never happened — and this log is the only
    # signal for exactly the cases the keep guards exist to handle.
    if result.get("regenerated", True):
        logger.info(
            "Generated video for experiment %s scan %s (%d frames)",
            experiment_id,
            scan_id,
            result["frames"],
        )
    else:
        logger.info(
            "Kept the stored video for experiment %s scan %s",
            experiment_id,
            scan_id,
        )
    return {"experiment_id": experiment_id, **result}


@app.post("/gravi/experiments/{experiment_id}/plate-video")
def gravi_plate_video(
    experiment_id: int,
    body: dict,
    user_id: str = Depends(require_supabase_user),
):
    """On-demand: render one plate's time-lapse for one wave.

    `plate_id` and `wave_number` travel in the body, not the path — `plate_id`
    is free text, and the cylinder route's integer-only path defence does not
    transfer to it.

    Requires a valid Supabase user JWT (Bearer). Rate-limited per user, which
    bounds how fast one account can ask — not how often a plate is rendered:
    five clicks inside the window are five renders. What absorbs a repeated
    click is the plate lock and the second look at the plan, both in
    `render_plate_video`.
    """
    # Read before anything can refuse, so a rejected body is still recorded as
    # a request for something. `%r` below because neither has been validated on
    # that path, and an unescaped newline in a plate id would forge a log line.
    asked = body if isinstance(body, dict) else {}

    try:
        enforce_rate_limit(user_id)
        result = plate_request.render(experiment_id, body)
    except HTTPException as exc:
        # A refusal is what a caller sees when the button appears to do nothing,
        # and it used to leave no trace: the line below is reached only when a
        # render returns, so the log held every request that worked and none of
        # the ones anyone would look up.
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
        "plate video for experiment %s plate %s wave %s requested by %s: %s",
        experiment_id,
        result.get("plate_id"),
        result.get("wave_number"),
        user_id,
        result["action"],
    )
    return result


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
