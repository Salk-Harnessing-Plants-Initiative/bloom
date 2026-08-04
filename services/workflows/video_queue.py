"""
Queue helpers for the async cyl-video path.

Everything goes through the public SECURITY DEFINER wrapper functions
(enqueue_cyl_video / claim_cyl_video_job / complete_cyl_video_job /
fail_cyl_video_job), called as the bloom_workflows app user via the Supabase
client — so neither the API nor the worker needs a direct DB connection or pgmq
grants. Enqueue is used by the route; claim/complete/fail by the worker.
"""

import os

from fastapi import HTTPException
from postgrest import APIError

from supabase_client import app_client
from video import scan_in_experiment

# Visibility timeout for a claimed message — longer than a worst-case encode so a
# healthy worker finishes before the message is redelivered.
VISIBILITY_TIMEOUT = int(os.environ.get("WORKFLOWS_WORKER_VT_SECONDS", "120"))


def enqueue_experiment_scan_video(experiment_id: int, scan_id: int) -> dict:
    """Validate the scan belongs to the experiment, then enqueue a video job."""
    client = app_client()
    if not scan_in_experiment(client, experiment_id, scan_id):
        raise HTTPException(
            status_code=404,
            detail=f"Scan {scan_id} not found in experiment {experiment_id}",
        )
    try:
        res = client.rpc(
            "enqueue_cyl_video",
            {"p_scan_id": scan_id, "p_experiment_id": experiment_id},
        ).execute()
    except APIError as exc:
        # 53400 = the backlog cap in enqueue_cyl_video fired (queue full).
        if getattr(exc, "code", "") == "53400" or "queue is full" in (
            getattr(exc, "message", "") or ""
        ):
            raise HTTPException(
                status_code=503,
                detail="Video generation queue is full — enqueue denied, try again later.",
                headers={"Retry-After": "60"},
            ) from exc
        raise
    return {"job_id": res.data, "status": "queued"}


def claim_job(client, vt: int = VISIBILITY_TIMEOUT):
    """Claim the next queued job (or None if the queue is empty)."""
    rows = client.rpc("claim_cyl_video_job", {"p_vt": vt}).execute().data or []
    return rows[0] if rows else None


def complete_job(client, job_id: str, msg_id: int, path: str) -> None:
    """Mark the job complete and drop the message."""
    client.rpc(
        "complete_cyl_video_job",
        {"p_job_id": job_id, "p_msg_id": msg_id, "p_path": path},
    ).execute()


def fail_job(client, job_id: str, msg_id: int, error) -> None:
    """Record a failure (retry via vt expiry, or dead-letter after max attempts)."""
    client.rpc(
        "fail_cyl_video_job",
        {"p_job_id": job_id, "p_msg_id": msg_id, "p_error": str(error)[:500]},
    ).execute()
