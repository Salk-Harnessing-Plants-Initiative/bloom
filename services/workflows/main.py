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
    POST /cyl/experiments/{experiment_id}/scans/{scan_id}/video/queue
                                                     - queued: enqueue the job and
                                                       return a job id immediately;
                                                       a worker generates it and
                                                       updates cyl_video_jobs
                                                       (requires a Supabase user JWT)
"""

import os
import logging

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from auth import require_supabase_user, enforce_rate_limit
from video import generate_experiment_scan_video
from video_queue import enqueue_experiment_scan_video

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
    logger.info(
        "Generated video for experiment %s scan %s (%d frames)",
        experiment_id,
        scan_id,
        result["frames"],
    )
    return {"experiment_id": experiment_id, **result}


@app.post("/cyl/experiments/{experiment_id}/scans/{scan_id}/video/queue")
def cyl_experiment_scan_video_queue(
    experiment_id: int,
    scan_id: int,
    user_id: str = Depends(require_supabase_user),
):
    """Queued: enqueue a cyl scan's video for the worker to generate.

    Returns immediately with a job id; poll cyl_video_jobs for status. Same auth
    (Supabase user JWT) and per-user rate limit as the on-demand route.
    """
    enforce_rate_limit(user_id)
    result = enqueue_experiment_scan_video(experiment_id, scan_id)
    logger.info(
        "Queued video job %s for experiment %s scan %s",
        result["job_id"],
        experiment_id,
        scan_id,
    )
    return {"experiment_id": experiment_id, "scan_id": scan_id, **result}
