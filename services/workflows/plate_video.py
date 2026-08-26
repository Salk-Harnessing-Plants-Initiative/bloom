"""Which frames a plate time-lapse would be made from.

A plate is identified by (experiment, plate, wave) — the page has no scan ids to
hand — so the frame set is looked up rather than supplied. Looking it up at
request time is also what keeps it current: a plate gains captures while the
experiment runs, and a list captured when the page rendered would encode a
frame set that is already behind.

No storage, no encoding. This module answers what there is to render.
"""

from __future__ import annotations

from datetime import datetime

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
        "capture_date": row["capture_date"],
        "cycle_number": row.get("cycle_number"),
        "session_id": row.get("session_id"),
        "object_path": (embedded or {}).get("object_path"),
        "file_size_bytes": (embedded or {}).get("file_size_bytes"),
    }


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


def missing_cycles(frames: list[dict]) -> list[int]:
    """Cycle numbers absent between the lowest and highest present.

    Bounded by what arrived rather than by the plan, so it holds whatever the
    numbering starts at. A run that stopped early has no gap — it has a short
    tail, which `planned_cycles` is what answers.
    """
    present = {f["cycle_number"] for f in frames if f["cycle_number"] is not None}
    if len(present) < 2:
        return []
    return sorted(set(range(min(present), max(present) + 1)) - present)


def completeness(frames: list[dict], planned: int | None) -> dict:
    """What arrived, against what was planned. Describes; never refuses.

    A short run still renders — the video is real, just shorter than intended.
    The point is that it can be said so, rather than looking finished.

    `state` separates a run known to be short from one that cannot be checked:
    both are "not complete", but only the first is a fact about the run.
    """
    present = len(frames)
    gaps = missing_cycles(frames)

    if planned is None:
        state = "unknown"
        summary = f"{present} frames; the run recorded no planned cycle count"
    elif gaps:
        listed = ", ".join(str(c) for c in gaps[:5])
        more = f" and {len(gaps) - 5} more" if len(gaps) > 5 else ""
        state = "gaps"
        summary = f"{present} of {planned} frames; missing cycles {listed}{more}"
    elif present < planned:
        state = "short"
        summary = f"{present} of {planned} frames; the run stopped early"
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


def too_large_to_render(frames: list[dict], limit: int = MAX_SOURCE_BYTES) -> str | None:
    """Why this plate cannot be rendered in a request, or None.

    Checked before anything is downloaded: the render is synchronous behind a
    240s proxy timeout, and a long plate is tens of gigabytes. The deadline
    would stop it eventually — this stops it in a millisecond, with a reason.
    """
    total, unknown = source_bytes(frames)
    if total <= limit:
        return None

    counted = f"{len(frames) - unknown} of {len(frames)} frames"
    return (
        f"this plate is at least {total / 1024**3:.1f} GB across {counted}, "
        f"over the {limit / 1024**3:.0f} GB a single render may pull"
    )


def first_capture(frames: list[dict]) -> datetime | None:
    """The instant the run's elapsed labels count from.

    Derived here rather than taken from the caller: `label_for` renders a
    negative elapsed instead of refusing, so a wrong start produces a video that
    is internally consistent and wrong throughout.
    """
    return frames[0]["capture_date"] if frames else None
