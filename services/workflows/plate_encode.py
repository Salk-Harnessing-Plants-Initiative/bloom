"""Turning a plate's stored images into a time-lapse.

Decode, downscale, then label — in that order. The label band is a fixed
height, so on a native 4960x6850 frame it is 0.6% of the picture and the text
is unreadable; after downscaling it is legible. Measured both ways on a real
plate before choosing.

One frame is in flight at a time. A plate TIFF is ~97 MB decoded and about
twice that while the label is added, so holding the set would be gigabytes.
"""

from __future__ import annotations

import io
import logging

import numpy as np
from PIL import Image

from plate_timelapse import PLATE_FPS, annotate, label_for
from plate_video import first_capture
from plate_video_path import GRAVISCAN_IMAGES_BUCKET
from video_writer import VideoWriter

logger = logging.getLogger(__name__)

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


class FrameUnreadable(RuntimeError):
    """A frame could not be fetched or decoded, and the render must not go on."""


def encode_plate_video(client, frames: list[dict], out_path: str) -> int:
    """Encode `frames` into an MP4 at `out_path`. Returns the frames written.

    Fails on the first unreadable frame rather than skipping it. `video.py`
    skips, which is defensible for a rotation — frames are interchangeable
    views of one moment. These are ordered in time, and the interesting one is
    wherever the root moved, so a dropped frame misrepresents the curve while
    the video still looks complete.
    """
    if not frames:
        raise FrameUnreadable("no frames to encode")

    started = first_capture(frames)
    images = client.storage.from_(GRAVISCAN_IMAGES_BUCKET)
    writer = VideoWriter(filename=out_path, fps=PLATE_FPS)
    written = 0

    try:
        for frame in frames:
            path = frame["object_path"]
            label = label_for(frame["capture_date"], started)
            writer.add(_fetch_frame(images, path, label))
            written += 1
        writer.close()
    except Exception:
        # Tear ffmpeg down without masking why we got here. The partial file is
        # left on disk; nothing uploads it, because nothing reaches the upload
        # unless this returns.
        try:
            writer.close()
        except Exception as cleanup:
            logger.warning("encoder cleanup after a failed render: %s", cleanup)
        raise

    logger.info("encoded %s frames to %s", written, out_path)
    return written


def _fetch_frame(images, path: str, label: str) -> np.ndarray:
    """One object, downloaded and prepared, or a failure naming it."""
    try:
        data = images.download(path)
    except Exception as exc:
        raise FrameUnreadable(f"could not download {path}: {exc}") from exc

    if not data:
        raise FrameUnreadable(f"{path} is empty")

    try:
        return prepare_frame(data, label)
    except Exception as exc:
        raise FrameUnreadable(f"could not decode {path}: {exc}") from exc
