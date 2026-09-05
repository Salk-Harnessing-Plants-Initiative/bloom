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

# `gravi_scans.wave_number` is a Postgres INT, so a larger number cannot be
# stored and can never match a row — the query errors rather than answering
# empty. A real wave is a small number; this is only where the arithmetic stops.
MAX_WAVE_NUMBER = 2**31 - 1


def render(experiment_id: int, body: dict) -> dict:
    """Render one plate's time-lapse, or explain why not."""
    plate_id, wave_number = _read(body)

    try:
        outcome = render_plate_video(app_client(), experiment_id, plate_id, wave_number)
    except EncoderBusy as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "30"}) from exc
    except FrameDepthUnsupported as exc:
        # Before FrameUnreadable, which it subclasses. Its own status because
        # the action differs: the file is intact and it is the scanner's output
        # depth this encoder does not cover, so 502 would send someone to look
        # for an upstream failure that did not happen.
        logger.warning("plate video refused an unsupported frame depth: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FrameUnreadable as exc:
        # Naming the frame is the point — "a frame failed" sends someone to the
        # scanner, "12/wave-1/P7_40.tif could not be read" sends them to it. The
        # path only: the rest of the message is the storage client's own error,
        # which carries the internal gateway host, the database role and
        # PostgREST's codes. Caddy publishes this service directly, so whatever
        # goes in `detail` reaches the caller unfiltered.
        logger.warning("plate video render failed: %s", exc)
        named = f"{exc.path} could not be read" if exc.path else "a frame could not be read"
        raise HTTPException(status_code=502, detail=named) from exc
    except NotRecorded as exc:
        # The message wraps the database client's error — the role name and
        # PostgREST's SQLSTATEs. The object key is the caller's own plate and is
        # worth naming; the rest is the log's business.
        logger.error("plate video stored but not recorded: %s", exc)
        named = f"the video for {exc.key} was not recorded" if exc.key else "the video was not recorded"
        raise HTTPException(status_code=500, detail=named) from exc
    except (VideoEncodeError, BrokenPipeError) as exc:
        # The encoder's own failures: a stall the watchdog killed, a non-zero
        # ffmpeg exit, a pipe that broke. Without this branch each arrives as an
        # unexplained 500 — including "ffmpeg accepted no frame for 120.0s and
        # was killed", which is the one line a caller waiting two minutes needs.
        logger.error("plate video encode failed: %s", exc)
        raise HTTPException(
            status_code=500, detail="the video could not be encoded"
        ) from exc
    except PlateMismatch as exc:
        # A crossed key and identity: this run would have stored one plate's
        # video under another's name. Nothing to retry, and nothing the caller
        # can act on, so the detail stays generic and the reason is logged.
        logger.error("plate video refused a crossed plate identity: %s", exc)
        raise HTTPException(
            status_code=500, detail="the video could not be stored"
        ) from exc

    if outcome["action"] == "refuse":
        raise HTTPException(
            status_code=_REFUSAL_STATUS.get(outcome.get("code"), 409),
            detail=outcome["reason"],
        )

    # The encoder's own count when something was encoded, and the planned list
    # otherwise — a keep or a refusal never reached the encoder. The two agree
    # today, because one unreadable frame fails the whole render; this is what
    # keeps the number honest if that ever stops being true.
    recorded = outcome.get("recorded") or {}

    return {
        "experiment_id": experiment_id,
        "plate_id": plate_id,
        "wave_number": wave_number,
        "action": outcome["action"],
        "reason": outcome["reason"],
        "object_path": outcome["key"],
        "frames": recorded.get("frame_count", len(outcome.get("frames") or [])),
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
    if wave_number > MAX_WAVE_NUMBER:
        raise HTTPException(
            status_code=400,
            detail=f"wave_number may not be greater than {MAX_WAVE_NUMBER}",
        )

    return plate_id, wave_number
