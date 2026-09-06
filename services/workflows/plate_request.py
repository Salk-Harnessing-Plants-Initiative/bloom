"""The HTTP shape of a plate-video request: validate, render, choose a status.

Kept out of `main.py` so the validation and the status mapping can be tested
without a TestClient, and out of `plate_encode.py` so that module stays free of
HTTP.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from plate_encode import (
    EncoderBusy,
    FrameDepthUnsupported,
    FrameTooLarge,
    FrameUnreadable,
    NotRecorded,
    PlateMismatch,
    render_plate_video,
)
from plate_video_path import is_valid_plate_id
from video_writer import VideoEncodeError
from supabase_client import app_client

logger = logging.getLogger(__name__)

# Why a render did not happen, and what to answer. A refusal is not a bug — the
# caller asked a reasonable question and the answer is no.
_REFUSAL_STATUS = {
    "storage_unavailable": 503,  # transient; the same request may succeed later
    "database_unavailable": 503,  # the same, one read further back
    "no_frames": 404,  # nothing to render for this plate and wave
    "unusable_plate": 400,  # the plate id cannot become an object key
    "too_large": 413,  # more than one request may pull
}

# The largest value the INT columns behind these can hold.
MAX_WAVE_NUMBER = 2**31 - 1
MAX_EXPERIMENT_ID = 2**31 - 1


def render(experiment_id: int, body: dict) -> dict:
    """Render one plate's time-lapse, or explain why not."""
    plate_id, wave_number = _read(body)

    if experiment_id < 1 or experiment_id > MAX_EXPERIMENT_ID:
        raise HTTPException(
            status_code=400,
            detail=f"experiment_id must be between 1 and {MAX_EXPERIMENT_ID}",
        )

    try:
        outcome = render_plate_video(app_client(), experiment_id, plate_id, wave_number)
    except EncoderBusy as exc:
        raise HTTPException(
            status_code=429, detail=str(exc), headers={"Retry-After": "30"}
        ) from exc
    except FrameDepthUnsupported as exc:
        # Before FrameUnreadable, which it subclasses. The file is intact.
        logger.warning("plate video refused an unsupported frame depth: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FrameTooLarge as exc:
        # Before FrameUnreadable, which it subclasses. The size is safe to send.
        logger.warning("plate video refused an oversized frame: %s", exc)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except FrameUnreadable as exc:
        # The path only: the rest is the storage client's error, which names
        # internal hosts and roles and reaches the caller unfiltered.
        logger.warning("plate video render failed: %s", exc)
        named = (
            f"{exc.path} could not be read" if exc.path else "a frame could not be read"
        )
        raise HTTPException(status_code=502, detail=named) from exc
    except NotRecorded as exc:
        # The key only, for the reason above.
        logger.error("plate video stored but not recorded: %s", exc)
        named = (
            f"the video for {exc.key} was not recorded"
            if exc.key
            else "the video was not recorded"
        )
        raise HTTPException(status_code=500, detail=named) from exc
    except (VideoEncodeError, BrokenPipeError) as exc:
        # The encoder's own failures: a stall, a non-zero exit, a broken pipe.
        logger.error("plate video encode failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="the video could not be encoded"
        ) from exc
    except PlateMismatch as exc:
        # A crossed key and identity. Nothing the caller can act on.
        logger.error("plate video refused a crossed plate identity: %s", exc)
        raise HTTPException(
            status_code=500, detail="the video could not be stored"
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        # A denied grant, a row that will not parse, a storage upload that
        # failed. Waiting does not fix any of them, so the caller is not told
        # to. The cause is the log's, with a traceback.
        logger.exception("plate video failed for an unhandled reason")
        raise HTTPException(
            status_code=500,
            detail="this video cannot be made right now — please reach out to "
            "the Bloom team",
        ) from exc

    if outcome["action"] == "refuse":
        raise HTTPException(
            status_code=_REFUSAL_STATUS.get(outcome.get("code"), 409),
            detail=outcome["reason"],
        )

    # What the video holds: the encoder's count when one was made, the stored
    # video's own when it was kept, and the plan only when neither exists.
    recorded = outcome.get("recorded") or {}
    held = recorded.get("frame_count", outcome.get("stored_frames"))

    return {
        "experiment_id": experiment_id,
        "plate_id": plate_id,
        "wave_number": wave_number,
        "action": outcome["action"],
        "reason": outcome["reason"],
        "object_path": outcome["key"],
        "frames": held if held is not None else len(outcome.get("frames") or []),
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
