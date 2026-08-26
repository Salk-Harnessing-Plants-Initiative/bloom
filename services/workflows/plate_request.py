"""The HTTP shape of a plate-video request: validate, render, choose a status.

Kept out of `main.py` so the validation and the status mapping can be tested
without a TestClient, and out of `plate_encode.py` so that module stays free of
HTTP.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from plate_encode import EncoderBusy, FrameUnreadable, NotRecorded, render_plate_video
from plate_video_path import is_valid_plate_id
from supabase_client import app_client

logger = logging.getLogger(__name__)

# Why a render did not happen, and what to answer. A refusal is not a bug — the
# caller asked a reasonable question and the answer is no.
_REFUSAL_STATUS = {
    "storage_unavailable": 503,  # transient; the same request may succeed later
    "no_frames": 404,  # nothing to render for this plate and wave
    "unusable_plate": 400,  # the plate id cannot become an object key
    "too_large": 413,  # more than one request may pull
}


def render(experiment_id: int, body: dict) -> dict:
    """Render one plate's time-lapse, or explain why not."""
    plate_id, wave_number = _read(body)

    try:
        outcome = render_plate_video(app_client(), experiment_id, plate_id, wave_number)
    except EncoderBusy as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "30"}) from exc
    except FrameUnreadable as exc:
        # Naming the frame is the point — "a frame failed" sends someone to the
        # scanner, "12/wave-1/P7_40.tif could not be downloaded" sends them to it.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except NotRecorded as exc:
        logger.error("plate video stored but not recorded: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if outcome["action"] == "refuse":
        raise HTTPException(
            status_code=_REFUSAL_STATUS.get(outcome.get("code"), 409),
            detail=outcome["reason"],
        )

    return {
        "experiment_id": experiment_id,
        "plate_id": plate_id,
        "wave_number": wave_number,
        "action": outcome["action"],
        "reason": outcome["reason"],
        "object_path": outcome["key"],
        "frames": len(outcome.get("frames") or []),
        "coverage": outcome.get("coverage"),
    }


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
            "dash or underscore, and may not begin with a dot",
        )

    wave_number = body.get("wave_number")
    if wave_number is None:
        return plate_id, None

    # bool is an int subclass, and True would become wave 1.
    if isinstance(wave_number, bool) or not isinstance(wave_number, int):
        raise HTTPException(status_code=400, detail="wave_number must be a whole number or null")
    if wave_number < 0:
        raise HTTPException(status_code=400, detail="wave_number may not be negative")

    return plate_id, wave_number
