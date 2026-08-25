"""Where a plate's time-lapse video lives.

Pure strings, no client and no network. The TypeScript copy in
web/lib/supabase/plate-video-path.ts must stay identical;
tests/unit/test_plate_video_path_agreement.py checks it.
"""

from __future__ import annotations

import os
import re

VIDEOS_BUCKET = os.environ.get("WORKFLOWS_PLATE_VIDEOS_BUCKET", "graviscan-videos")
IMAGES_BUCKET = os.environ.get("WORKFLOWS_PLATE_IMAGES_BUCKET", "graviscan-images")

# A plate id is free text and becomes a path segment, so it is whitelisted
# rather than escaped. No leading dot, so `..` cannot form.
PLATE_ID_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

_PLATE_ID_RE = re.compile(PLATE_ID_PATTERN)


def is_valid_plate_id(plate_id: str) -> bool:
    """True when `plate_id` is safe to place in an object key."""
    return isinstance(plate_id, str) and _PLATE_ID_RE.fullmatch(plate_id) is not None


def wave_segment(wave_number: int | None) -> str | None:
    """The path segment for a wave, or None when the wave is unusable.

    A plate with no wave still needs a segment; an empty one would collide with
    the experiment's own level.
    """
    if wave_number is None:
        return "wave-none"
    # bool is an int subclass, and True would render as `wave-True`.
    if isinstance(wave_number, bool) or not isinstance(wave_number, int):
        return None
    if wave_number < 0:
        return None
    return f"wave-{wave_number}"


def plate_video_path(
    experiment_id: int, wave_number: int | None, plate_id: str
) -> str | None:
    """The object key for one plate's video, or None if any part is unusable."""
    if isinstance(experiment_id, bool) or not isinstance(experiment_id, int):
        return None
    if experiment_id <= 0:
        return None
    if not is_valid_plate_id(plate_id):
        return None

    wave = wave_segment(wave_number)
    if wave is None:
        return None

    return f"{experiment_id}/{wave}/{plate_id}.mp4"
