"""Which frames a plate time-lapse would be made from.

A plate is identified by (experiment, plate, wave) — the page has no scan ids to
hand — so the frame set is looked up rather than supplied. Looking it up at
request time is also what keeps it current: a plate gains captures while the
experiment runs, and a list captured when the page rendered would encode a
frame set that is already behind.

No storage, no encoding. This module answers what there is to render.
"""

from __future__ import annotations

import logging
from datetime import datetime

from plate_video_path import GRAVISCAN_VIDEOS_BUCKET, plate_video_path

logger = logging.getLogger(__name__)

# Storage answers a missing object with an error rather than an empty result,
# and with HTTP 400 carrying a string code — so the wording is the real guard.
# Every token has to name the object. Storage answers a missing bucket with
# "Bucket not found" and the same `not_found` error code as a missing object,
# so a bare "not found" or "not_found" reads a broken bucket as an absent
# video and renders into a bucket that cannot accept it.
_NOT_FOUND = ("object not found", "no such key", "object does not exist")

# The scan carries the capture time and the plate's identity; the image carries
# the object to download. `!inner` drops a capture whose image never arrived —
# excluded in the query rather than skipped later, so `len(frames)` means the
# same thing as the count recorded against a stored video.
_FRAME_SELECT = (
    "capture_date, cycle_number, session_id, "
    "gravi_images!inner(object_path, file_size_bytes)"
)

# Download volume, not memory. These are LZW TIFFs: ~57 MB on disk and ~97 MB
# decoded, so ~86 frames is roughly 5 GB to pull. A coarse backstop against a
# multi-day plate — the deadline is what actually bounds a render. Tune on staging.
MAX_SOURCE_BYTES = 8 * 1024**3

# What a frame that recorded no size is assumed to weigh. Live frames measure
# 45-64 MB, so this is the middle of the range rather than a guess.
NOMINAL_FRAME_BYTES = 57 * 1024**2


def get_plate_frames(
    client,
    experiment_id: int,
    plate_id: str,
    wave_number: int | None,
) -> list[dict]:
    """A plate's encodable captures, oldest first.

    Ordered by `capture_date` because that is the only total order available: it
    is NOT NULL and unique per (experiment, plate), while `cycle_number` is
    nullable. Time order is also the order the video has to play in.
    """
    query = (
        client.table("gravi_scans")
        .select(_FRAME_SELECT)
        .eq("experiment_id", experiment_id)
        .eq("plate_id", plate_id)
    )
    # A plate with no wave is a real case, and `= NULL` matches nothing.
    query = (
        query.is_("wave_number", "null")
        if wave_number is None
        else query.eq("wave_number", wave_number)
    )

    rows = query.order("capture_date").execute().data or []
    return [_frame(row) for row in rows]


def _frame(row: dict) -> dict:
    """One capture, with the embedded image flattened onto it.

    PostgREST returns an embedded resource as a nested object, or a list of one
    when it cannot prove the relationship is to-one. `gravi_images.scan_id` is
    UNIQUE, so there is only ever one image; both shapes are read the same way.
    """
    embedded = row.get("gravi_images")
    if isinstance(embedded, list):
        embedded = embedded[0] if embedded else {}

    return {
        "capture_date": _capture_datetime(row["capture_date"]),
        "cycle_number": row.get("cycle_number"),
        "session_id": row.get("session_id"),
        "object_path": (embedded or {}).get("object_path"),
        "file_size_bytes": (embedded or {}).get("file_size_bytes"),
    }


def _capture_datetime(value) -> datetime:
    """`capture_date` as a datetime.

    PostgREST sends TIMESTAMPTZ as an ISO string, and the label maths needs a
    datetime. Converted here, where rows enter, so nothing downstream has to
    know which it was handed. A naive value stays naive on purpose: `label_for`
    refuses one rather than guess a zone.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise TypeError(
            f"capture_date must be a datetime or an ISO string, got {value!r}"
        )
    try:
        # 3.11 is the floor, and its fromisoformat takes a Z-terminated offset.
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"unparseable capture_date {value!r}") from exc


def planned_cycles(client, frames: list[dict]) -> int | None:
    """How many captures the run that produced these frames set out to take.

    None when the frames name no session, or the session recorded no plan —
    both mean "unknown", which is not the same as "nothing is missing".
    """
    session_ids = {f["session_id"] for f in frames if f["session_id"] is not None}
    if not session_ids:
        return None

    rows = (
        client.table("gravi_scan_sessions")
        .select("total_cycles")
        .in_("id", sorted(session_ids))
        .execute()
        .data
        or []
    )
    planned = [r["total_cycles"] for r in rows if r.get("total_cycles") is not None]
    # A plate scanned across more than one session planned the sum of them.
    return sum(planned) if planned else None


def session_first_cycle(client, frames: list[dict]) -> int | None:
    """The lowest cycle number any plate in this run recorded.

    Every plate in a run is photographed on every cycle, so a sibling plate's
    lowest cycle is where this plate's numbering should have started. That is
    what makes a missing first frame detectable: this plate's own frames cannot
    show it.

    None when the frames name no session, or no cycle was recorded — both mean
    the start is unknown, and `missing_cycles` then falls back to what arrived.

    Blind in one case, deliberately: if the run lost the same leading cycles for
    every plate, the siblings agree and the hole stays invisible.
    """
    session_ids = {f["session_id"] for f in frames if f["session_id"] is not None}
    if not session_ids:
        return None

    rows = (
        client.table("gravi_scans")
        .select("cycle_number")
        .in_("session_id", sorted(session_ids))
        .execute()
        .data
        or []
    )
    cycles = [r["cycle_number"] for r in rows if r.get("cycle_number") is not None]
    return min(cycles) if cycles else None


def missing_cycles(frames: list[dict], first_cycle: int | None = None) -> list[int]:
    """Cycle numbers absent between `first_cycle` and the highest present.

    Without `first_cycle` the search starts at the lowest cycle that arrived,
    which cannot see a hole at the start of a run — the question would be
    "are any of my cycles missing?" asked using only the cycles it has.
    `session_first_cycle` supplies the bound a sibling plate proves.

    A run that stopped early has no gap — it has a short tail, which
    `planned_cycles` is what answers.

    A plate is scanned once per wave, so cycle numbers are unique within a
    frame set and can be compared directly.
    """
    present = {f["cycle_number"] for f in frames if f["cycle_number"] is not None}
    if not present:
        return []

    low = min(present) if first_cycle is None else min(first_cycle, min(present))
    return sorted(set(range(low, max(present) + 1)) - present)


def completeness(
    frames: list[dict], planned: int | None, first_cycle: int | None = None
) -> dict:
    """What arrived, against what was planned. Describes; never refuses.

    A short run still renders — the video is real, just shorter than intended.
    The point is that it can be said so, rather than looking finished.

    `state` separates a run known to be short from one that cannot be checked:
    both are "not complete", but only the first is a fact about the run.
    """
    present = len(frames)
    gaps = missing_cycles(frames, first_cycle)

    if planned is None:
        state = "unknown"
        summary = f"{present} frames; the run recorded no planned cycle count"
    elif gaps:
        listed = ", ".join(str(c) for c in gaps[:5])
        more = f" and {len(gaps) - 5} more" if len(gaps) > 5 else ""
        state = "gaps"
        summary = f"{present} of {planned} frames; missing cycles {listed}{more}"
    elif present < planned:
        # Says what arrived, not why. Frames upload after a run finishes, so a
        # short count during that window is the normal state, not a fault.
        state = "short"
        summary = f"{present} of {planned} frames so far"
    else:
        # A run commonly outlasts its planned cycle count, so the plan is a
        # floor rather than a target. Say the real number either way.
        state = "complete"
        ran_on = f", past the {planned} planned" if present > planned else ""
        summary = f"{present} frames{ran_on}"

    return {
        "present": present,
        "planned": planned,
        "missing_cycles": gaps,
        "state": state,
        "summary": summary,
        "complete": state == "complete",
    }


def source_bytes(frames: list[dict]) -> tuple[int, int]:
    """Bytes to download, and how many frames did not say.

    `file_size_bytes` is nullable, so the total is a floor: safe to refuse on,
    never safe to read as "small enough".
    """
    known = [f["file_size_bytes"] for f in frames if f["file_size_bytes"] is not None]
    return sum(known), len(frames) - len(known)


def too_large_to_render(
    frames: list[dict], limit: int = MAX_SOURCE_BYTES
) -> str | None:
    """Why this plate cannot be rendered in a request, or None.

    Checked before anything is downloaded: the render is synchronous behind a
    240s proxy timeout, and a long plate is tens of gigabytes. The deadline
    would stop it eventually — this stops it in a millisecond, with a reason.

    A frame that recorded no size is estimated rather than counted as zero,
    from this plate's own frames where it can be. Reading a missing size as
    nothing lets a plate of any size past the guard.
    """
    total, unknown = source_bytes(frames)
    counted = len(frames) - unknown

    if unknown:
        per_frame = total // counted if counted else NOMINAL_FRAME_BYTES
        total += unknown * per_frame

    if total <= limit:
        return None

    measured = (
        f"{counted} of {len(frames)} frames" if unknown else f"all {len(frames)} frames"
    )
    return (
        f"this plate is about {total / 1024**3:.1f} GB, measured across "
        f"{measured}, over the {limit / 1024**3:.0f} GB a single render may pull"
    )


def first_capture(frames: list[dict]) -> datetime | None:
    """The instant the run's elapsed labels count from.

    Derived here rather than taken from the caller: `label_for` renders a
    negative elapsed instead of refusing, so a wrong start produces a video that
    is internally consistent and wrong throughout.
    """
    return frames[0]["capture_date"] if frames else None


# --- what is already stored --------------------------------------------------


def stored_video(
    client, experiment_id: int, plate_id: str, wave_number: int | None
) -> dict:
    """What this plate already has: `present`, `absent`, or `unknown`.

    `unknown` is neither, and must not collapse into either. Read as absent, a
    storage outage overwrites a good video with whatever this run manages to
    encode — and the same outage is skipping frame downloads, so an unclear
    answer arrives exactly when this render is the worse one. The key is
    deterministic, so the overwrite is in place and there is nothing to recover.
    Read as present, the request hands back a URL it cannot sign.
    """
    key = plate_video_path(experiment_id, wave_number, plate_id)
    if key is None:
        return {"state": "absent", "key": None, "frame_count": None}

    row = _recorded_video(client, experiment_id, plate_id, wave_number)
    if row is None:
        # No record. The object could still be there from a hand-run backfill,
        # but nothing says how many frames it covers, so it cannot be compared.
        return {"state": "absent", "key": key, "frame_count": None}

    exists = _object_exists(client, key)
    if exists is None:
        return {"state": "unknown", "key": key, "frame_count": row.get("frame_count")}
    if not exists:
        # The row outlived its object — regenerate rather than serve a 404.
        return {"state": "absent", "key": key, "frame_count": None}

    return {"state": "present", "key": key, "frame_count": row.get("frame_count")}


def _recorded_video(client, experiment_id: int, plate_id: str, wave_number: int | None):
    """The row recording this plate's video, or None."""
    query = (
        client.table("gravi_plate_videos")
        .select("object_path, frame_count")
        .eq("experiment_id", experiment_id)
        .eq("plate_id", plate_id)
    )
    query = (
        query.is_("wave_number", "null")
        if wave_number is None
        else query.eq("wave_number", wave_number)
    )
    rows = query.limit(1).execute().data or []
    return rows[0] if rows else None


def _object_exists(client, key: str) -> bool | None:
    """Whether an object sits at `key`, or None when storage could not say."""
    try:
        client.storage.from_(GRAVISCAN_VIDEOS_BUCKET).create_signed_url(key, 60)
        return True
    except Exception as exc:
        if any(token in str(exc).lower() for token in _NOT_FOUND):
            return False
        logger.warning("could not check for a stored video at %s: %s", key, exc)
        return None


# --- what to do about it -----------------------------------------------------


def render_decision(frames: list[dict], stored: dict) -> dict:
    """Whether to render this plate, hand back what is stored, or refuse.

    Frames upload from the scanner after the run finishes, so a video rendered
    before that upload completed is short rather than wrong. The recorded count
    is what tells those apart, and every branch here turns on having one.
    """
    available = len(frames)
    state = stored["state"]
    key = stored["key"]

    if state == "unknown":
        # Says what to do, not what broke: nothing here is the scientist's to
        # fix, and rendering on an answer we do not trust could replace a good
        # video with a worse one.
        return _outcome(
            "refuse",
            "this video cannot be made right now — storage did not answer. "
            "Nothing has been changed; try again in a few minutes",
            key,
        )

    if not available:
        return _outcome("refuse", "this plate has no captures with an image", key)

    if key is None:
        return _outcome("refuse", "this plate's video has no usable object key", key)

    if state == "absent":
        return _outcome("render", f"no video stored; encoding {available} frames", key)

    recorded = stored["frame_count"]
    if recorded is None:
        # Nothing to compare against, and keeping it would never self-correct:
        # with no count, no later request could beat it either. One render
        # replaces it with a video whose coverage is recorded.
        return _outcome(
            "render",
            "the stored video records no frame count; encoding to replace it",
            key,
        )

    if available > recorded:
        return _outcome(
            "render",
            f"{available - recorded} new frames since the stored video's {recorded}",
            key,
        )

    return _outcome(
        "keep", f"the stored video already covers {recorded} of {available} frames", key
    )


def _outcome(action: str, reason: str, key: str | None) -> dict:
    return {"action": action, "reason": reason, "key": key}


# --- the whole question, in one call -----------------------------------------


def plan_render(
    client, experiment_id: int, plate_id: str, wave_number: int | None
) -> dict:
    """Everything the caller needs to know before encoding anything.

    `client` is an argument rather than a module global so a worker can call
    this later without a refactor.

    The size check applies to the decision, not the request: a plate too large
    to encode still has its stored video handed back. Coverage is only computed
    when something will be rendered — on the keep path nothing reads it, and it
    costs a second query.
    """
    frames = get_plate_frames(client, experiment_id, plate_id, wave_number)
    stored = stored_video(client, experiment_id, plate_id, wave_number)
    decision = render_decision(frames, stored)

    if decision["action"] != "render":
        return {**decision, "frames": frames, "coverage": None}

    oversized = too_large_to_render(frames)
    if oversized:
        return {
            "action": "refuse",
            "reason": oversized,
            "key": stored["key"],
            "frames": frames,
            "coverage": None,
        }

    coverage = completeness(
        frames,
        planned_cycles(client, frames),
        session_first_cycle(client, frames),
    )
    return {**decision, "frames": frames, "coverage": coverage}
