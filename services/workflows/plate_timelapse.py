"""Frame rate and the burned-in timestamp for a plate time-lapse.

Pure functions — no client, no storage, no database. The label is drawn here
rather than by ffmpeg's `drawtext` because frames stream over stdin, so there
are no per-frame filenames for a filter to key on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# One frame is ~7 minutes of real time, so 4 fps is a quarter second per frame.
# Fixed rather than derived: every video plays at the same rate, so duration
# reflects how long the run was and two runs are directly comparable.
PLATE_FPS = 4

# The scanners are at Salk, and the plate pages render capture times in the
# viewer's own zone — which for a scientist there is this one. A video is a
# file and cannot know who is watching, so it names the zone it used.
PLATE_TIMEZONE = ZoneInfo("America/Los_Angeles")

# The band the label sits in, so it never covers tissue and never moves.
LABEL_BAND_HEIGHT = 44
LABEL_FONT_SIZE = 16
LABEL_PADDING = 8

_BAND_FILL = (0, 0, 0)
_TEXT_FILL = (255, 255, 255)


def label_for(capture_date: datetime, first_capture: datetime) -> str:
    """Two lines: when the frame was taken, and how long into the run it is.

    The absolute time is shown in the scanners' zone and names it, matching
    what the plate pages render. The elapsed line is timezone-free, and is what
    makes an irregular capture gap visible rather than silent.
    """
    absolute = _as_utc(capture_date).astimezone(PLATE_TIMEZONE)
    start = _as_utc(first_capture)

    elapsed = absolute - start
    sign = "-" if elapsed.total_seconds() < 0 else "+"
    # Floor the magnitude, not the signed value: flooring -90s gives -2 minutes.
    total_minutes = int(abs(elapsed).total_seconds() // 60)
    days, rem_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)

    if days:
        elapsed_text = f"{sign}{days}d {hours:02d}h {minutes:02d}m"
    else:
        elapsed_text = f"{sign}{hours:02d}h {minutes:02d}m"

    return f"{absolute:%Y-%m-%d %H:%M %Z}\n{elapsed_text}"


def annotate(frame: np.ndarray, label: str) -> np.ndarray:
    """Return `frame` with a labelled band added below it.

    The specimen is never drawn over — the band is extra rows underneath, so
    the frame's own pixels come back byte for byte. The result is taller than
    the input by LABEL_BAND_HEIGHT, and every frame grows by the same amount,
    so the video's dimensions stay constant.

    Takes 8-bit frames only. An MP4 is 8-bit regardless, so reducing a deeper
    frame is the decoder's job, where the full range is still known — doing it
    here would silently clamp highlights instead of scaling them.
    """
    if frame.ndim == 2:
        channels = 1
    elif frame.ndim == 3 and frame.shape[2] == 3:
        channels = 3
    else:
        raise ValueError(
            f"expected a 2D frame or a 3-channel 3D frame, got shape {frame.shape}"
        )

    if frame.dtype != np.uint8:
        raise ValueError(
            f"expected an 8-bit frame, got {frame.dtype} — reduce it before "
            "annotating so the full range is scaled rather than clamped"
        )

    height, width = frame.shape[:2]
    band = Image.new("RGB", (width, LABEL_BAND_HEIGHT), _BAND_FILL)
    ImageDraw.Draw(band).multiline_text(
        (LABEL_PADDING, LABEL_PADDING),
        label,
        fill=_TEXT_FILL,
        font=ImageFont.load_default(size=LABEL_FONT_SIZE),
        spacing=2,
    )

    band_array = np.asarray(band, dtype=np.uint8)
    if channels == 1:
        # The band is drawn in RGB because the text is; flatten it to match a
        # grayscale frame rather than widening the frame to colour.
        band_array = band_array[:, :, 0]

    return np.concatenate([frame, band_array], axis=0)


def _as_utc(value: datetime) -> datetime:
    """A naive datetime is read as UTC; nothing records another zone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
