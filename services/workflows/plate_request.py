"""The HTTP shape of a plate-video request: validate, then queue a render.

Kept out of `main.py` so the validation can be tested without a TestClient,
and out of `plate_encode.py` so that module stays free of HTTP.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from plate_video_path import is_valid_plate_id
from supabase_client import SINGLE_ROW_RPC_TIMEOUT_SECONDS, app_client
from video_queue import PLATE_QUEUE, enqueue as enqueue_job

logger = logging.getLogger(__name__)

# The status each planner refusal would answer over HTTP. The worker's render
# command reads them as exit codes. A refusal is not a bug — the caller asked a
# reasonable question and the answer is no.
REFUSAL_STATUS = {
    "storage_unavailable": 503,  # transient; the same request may succeed later
    "database_unavailable": 503,  # the same, one read further back
    "no_frames": 404,  # nothing to render for this plate and wave
    "unusable_plate": 400,  # the plate id cannot become an object key
    "too_large": 413,  # more than one request may pull
}

# The largest value the INT columns behind these can hold.
MAX_WAVE_NUMBER = 2**31 - 1
MAX_EXPERIMENT_ID = 2**31 - 1

# The database's own error names the role and PostgREST's codes, so callers get this.
NOT_QUEUED = "The video could not be queued right now. Try again shortly."


def enqueue(experiment_id: int, body: dict, user_id: str) -> dict:
    """Queue a render of one plate, or join the one already queued: {job_id, created}."""
    plate_id, wave_number = _read(body)

    if experiment_id < 1 or experiment_id > MAX_EXPERIMENT_ID:
        raise HTTPException(
            status_code=400,
            detail=f"experiment_id must be between 1 and {MAX_EXPERIMENT_ID}",
        )

    try:
        client = app_client(timeout_seconds=SINGLE_ROW_RPC_TIMEOUT_SECONDS)
        return enqueue_job(
            client,
            PLATE_QUEUE,
            {
                "p_experiment_id": experiment_id,
                "p_plate_id": plate_id,
                "p_wave_number": wave_number,
                "p_requested_by": user_id,
            },
        )
    except Exception as exc:
        logger.error("plate video could not be queued: %r", exc)
        raise HTTPException(status_code=503, detail=NOT_QUEUED) from exc


def _read(body: dict) -> tuple[str, int | None]:
    """`plate_id` and `wave_number` out of the request body.

    Re-validated here rather than trusted from the Next proxy: Caddy publishes
    this service directly, so that hop is not a security boundary. The plate id
    becomes an object key, and the rule is the one pinned across both languages.
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")

    plate_id = body.get("plate_id")
    if not is_valid_plate_id(plate_id):
        raise HTTPException(
            status_code=400,
            detail="plate_id must be 1-64 characters of letters, digits, dot, "
            "dash or underscore, and must begin with a letter or digit",
        )

    wave_number = body.get("wave_number")
    if wave_number is None:
        return plate_id, None

    # bool is an int subclass, and True would become wave 1.
    if isinstance(wave_number, bool) or not isinstance(wave_number, int):
        raise HTTPException(
            status_code=400, detail="wave_number must be a whole number or null"
        )
    if wave_number < 0:
        raise HTTPException(status_code=400, detail="wave_number may not be negative")
    if wave_number > MAX_WAVE_NUMBER:
        raise HTTPException(
            status_code=400,
            detail=f"wave_number may not be greater than {MAX_WAVE_NUMBER}",
        )

    return plate_id, wave_number
