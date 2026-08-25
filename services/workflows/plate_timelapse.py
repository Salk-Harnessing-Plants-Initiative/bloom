"""Frame rate and the burned-in timestamp for a plate time-lapse.

Pure functions — no client, no storage, no database. The label is drawn here
rather than by ffmpeg's `drawtext` because frames stream over stdin, so there
are no per-frame filenames for a filter to key on.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# One frame is ~7 minutes of real time, so 4 fps is a quarter second per frame.
# Fixed rather than derived: every video plays at the same rate, so duration
# reflects how long the run was and two runs are directly comparable.
PLATE_FPS = 4

# The band the label sits in, so it never covers tissue and never moves.
LABEL_BAND_HEIGHT = 44
LABEL_FONT_SIZE = 16
LABEL_PADDING = 8

_BAND_FILL = (0, 0, 0)
_TEXT_FILL = (255, 255, 255)


def label_for(capture_date: datetime, first_capture: datetime) -> str:
    """Two lines: when the frame was taken, and how long into the run it is.

    The absolute time says UTC explicitly because nothing in the schema records
    an experiment's timezone. The elapsed line is timezone-free, and is what
    makes an irregular capture gap visible rather than silent.
    """
    absolute = _as_utc(capture_date)
    start = _as_utc(first_capture)

    elapsed = absolute - start
    total_minutes = int(elapsed.total_seconds() // 60)
    sign = "-" if total_minutes < 0 else "+"
    total_minutes = abs(total_minutes)
    days, rem_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rem_minutes, 60)

    if days:
        elapsed_text = f"{sign}{days}d {hours:02d}h {minutes:02d}m"
    else:
        elapsed_text = f"{sign}{hours:02d}h {minutes:02d}m"

    return f"{absolute:%Y-%m-%d %H:%M} UTC\n{elapsed_text}"


def annotate(frame: np.ndarray, label: str) -> np.ndarray:
    """Draw `label` into a band along the bottom of `frame`.

    Returns an array of the same shape and dtype, so the caller can hand it
    straight to the encoder.
    """
    if frame.ndim not in (2, 3):
        raise ValueError(f"expected a 2D or 3D frame, got shape {frame.shape}")

    image = Image.fromarray(frame)
    was_grayscale = image.mode != "RGB"
    if was_grayscale:
        image = image.convert("RGB")

    band_top = max(0, image.height - LABEL_BAND_HEIGHT)
    draw = ImageDraw.Draw(image)
    draw.rectangle([(0, band_top), (image.width, image.height)], fill=_BAND_FILL)
    draw.multiline_text(
        (LABEL_PADDING, band_top + LABEL_PADDING),
        label,
        fill=_TEXT_FILL,
        font=ImageFont.load_default(size=LABEL_FONT_SIZE),
        spacing=2,
    )

    if was_grayscale:
        image = image.convert(Image.fromarray(frame).mode)
    return np.asarray(image, dtype=frame.dtype)


def _as_utc(value: datetime) -> datetime:
    """A naive datetime is read as UTC; nothing records another zone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
