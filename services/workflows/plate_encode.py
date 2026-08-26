"""Turning one stored image into one frame of a plate time-lapse.

Decode, downscale, then label — in that order. The label band is a fixed
height, so on a native 4960x6850 frame it is 0.6% of the picture and the text
is unreadable; after downscaling it is legible. Measured both ways on a real
plate before choosing.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from plate_timelapse import annotate

# Wide enough to make out individual roots — 720 was checked against a real
# plate and they are not distinguishable. A whole run is about 3.5 MB at this
# width, so file size does not constrain the choice; browser decode limits and
# legibility do.
PLATE_VIDEO_WIDTH = 1440


def prepare_frame(image_bytes: bytes, label: str) -> np.ndarray:
    """One stored image as a labelled, encodable frame.

    Returns 8-bit RGB, `PLATE_VIDEO_WIDTH` wide, with the label band beneath.
    """
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.load()
        rgb = _to_8bit_rgb(image)
        scaled = _downscale(rgb)
        frame = np.asarray(scaled, dtype=np.uint8)

    return annotate(frame, label)


def _to_8bit_rgb(image: Image.Image) -> Image.Image:
    """Three 8-bit channels, whatever the source was.

    A deeper source is scaled by its own range rather than converted directly:
    Pillow's `convert("RGB")` clamps, so a 16-bit frame would come back as a
    field of 255 wherever it exceeded 8 bits.
    """
    if image.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        data = np.asarray(image, dtype=np.float64)
        ceiling = data.max()
        scaled = data * (255.0 / ceiling) if ceiling > 0 else data
        image = Image.fromarray(scaled.astype(np.uint8), mode="L")

    return image if image.mode == "RGB" else image.convert("RGB")


def _downscale(image: Image.Image) -> Image.Image:
    """Narrowed to `PLATE_VIDEO_WIDTH`, aspect kept, both sides even.

    Never enlarged: a plate smaller than the target is left alone rather than
    interpolated up to it. H.264 needs even dimensions, and `VideoWriter` pads
    an odd frame by repeating its edge — doing it here keeps that off the label.
    """
    if image.width <= PLATE_VIDEO_WIDTH:
        return _even(image)

    height = round(image.height * PLATE_VIDEO_WIDTH / image.width)
    return _even(image.resize((PLATE_VIDEO_WIDTH, max(height, 1)), Image.LANCZOS))


def _even(image: Image.Image) -> Image.Image:
    width, height = image.width - image.width % 2, image.height - image.height % 2
    if (width, height) == image.size:
        return image
    return image.crop((0, 0, max(width, 2), max(height, 2)))
