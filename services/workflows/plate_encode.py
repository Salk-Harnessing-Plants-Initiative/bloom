"""Turning a plate's stored images into a time-lapse.

Decode, downscale, then label — in that order. The label band is a fixed
height, so on a native 4960x6850 frame it is 0.6% of the picture and the text
is unreadable; after downscaling it is legible. Measured both ways on a real
plate before choosing.

One frame is in flight at a time. A plate TIFF is ~97 MB decoded and about
twice that while the label is added, so holding the set would be gigabytes.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import tempfile
import threading
from contextlib import contextmanager

import numpy as np
from PIL import Image

from plate_timelapse import PLATE_FPS, annotate, label_for
from plate_video import first_capture, plan_render
from plate_video_path import GRAVISCAN_IMAGES_BUCKET, GRAVISCAN_VIDEOS_BUCKET
from video_writer import VideoWriter

logger = logging.getLogger(__name__)

# Wide enough to make out individual roots — 720 was checked against a real
# plate and they are not distinguishable. A whole run is about 3.5 MB at this
# width, so file size does not constrain the choice; browser decode limits and
# legibility do.
PLATE_VIDEO_WIDTH = 1440

# How many plates may encode at once. Not a memory bound — the host has 633 GB
# free and a render peaks around 194 MB. This is so forty simultaneous clicks
# do not saturate the link to storage and make every other request slower.
MAX_CONCURRENT_ENCODES = 4

# Nothing here bounds a single download. The storage client takes no per-call
# timeout, so that belongs on its HTTP client rather than in this module — the
# cyl path has the same gap. A whole plate is deliberately unbounded either
# way: 86 frames measured at 0.68s each, and a longer run should take longer
# rather than be truncated.

_encode_slots = threading.BoundedSemaphore(MAX_CONCURRENT_ENCODES)
_plate_locks: dict[str, threading.Lock] = {}
_plate_locks_guard = threading.Lock()


def plate_lock(key: str) -> threading.Lock:
    """The lock for one plate's video, created on first use.

    Keyed by the object key, so two requests for the same plate serialise while
    different plates do not. Taken once the plate is known to exist, so the
    table stays bounded by real plates rather than by anything a caller asks for.
    """
    with _plate_locks_guard:
        return _plate_locks.setdefault(key, threading.Lock())


@contextmanager
def encode_slot(timeout: float | None = None):
    """One of the concurrent-encode slots, or a refusal.

    Refusing beats queueing: the caller is a synchronous request that already
    has a client waiting, and a queue would hold the slot's worth of memory
    while achieving nothing the client can see.
    """
    if not _encode_slots.acquire(blocking=timeout is not None, timeout=timeout):
        raise EncoderBusy(
            f"{MAX_CONCURRENT_ENCODES} plate videos are already encoding; try again shortly"
        )
    try:
        yield
    finally:
        _encode_slots.release()


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


class EncoderBusy(RuntimeError):
    """Every encode slot is taken. Not a failure — a reason to come back."""


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


# --- publishing --------------------------------------------------------------


class NotRecorded(RuntimeError):
    """The video is stored but its row was not written."""


def publish_plate_video(
    client,
    key: str,
    video_path: str,
    *,
    experiment_id: int,
    plate_id: str,
    wave_number: int | None,
    frame_count: int,
) -> dict:
    """Upload the encoded video, then record what was made.

    Upload first. These are two systems with no shared transaction, so a crash
    between them leaves one of two orphans, and both are repaired by the next
    request — the stored-object probe checks the row and the object. The
    difference is what a scientist sees meanwhile: the page reads the row and
    does not check the object, so a row without its file renders a broken
    player, while a file without its row simply reads as no video yet.
    """
    with open(video_path, "rb") as handle:
        video = handle.read()

    if not video:
        raise NotRecorded(f"the encoder produced an empty file at {video_path}")

    videos = client.storage.from_(GRAVISCAN_VIDEOS_BUCKET)
    videos.upload(key, video, {"content-type": "video/mp4", "upsert": "true"})

    recorded = {
        "p_experiment_id": experiment_id,
        "p_plate_id": plate_id,
        "p_wave_number": wave_number,
        "p_object_path": key,
        "p_frame_count": frame_count,
        "p_duration_seconds": math.ceil(frame_count / PLATE_FPS),
        "p_fps": PLATE_FPS,
        "p_file_size_bytes": len(video),
        "p_file_hash": hashlib.sha256(video).hexdigest(),
    }

    try:
        client.rpc("record_gravi_plate_video", recorded).execute()
    except Exception as exc:
        # `video.py` logs and carries on here. That is what kept a recording
        # failure invisible until production held no rows against 84,748 stored
        # videos. Reporting success for a video the page cannot find is worse
        # than an error the caller can retry: the object is already stored, so
        # the next attempt overwrites it and records the row.
        raise NotRecorded(f"{key} was stored but recording it failed: {exc}") from exc

    logger.info("recorded %s: %s frames", key, frame_count)
    return {k.removeprefix("p_"): v for k, v in recorded.items()}


# --- the whole render --------------------------------------------------------


def render_plate_video(
    client, experiment_id: int, plate_id: str, wave_number: int | None
) -> dict:
    """Decide, encode, store, record. The one call a route makes.

    The plan is taken twice: once to decide, and again once the plate's lock is
    held. Between those two a concurrent request for the same plate may have
    rendered it, and re-encoding would overwrite a video identical to the one
    about to be made — the second look turns that into a `keep`.
    """
    plan = plan_render(client, experiment_id, plate_id, wave_number)
    if plan["action"] != "render":
        return plan

    with encode_slot(), plate_lock(plan["key"]):
        plan = plan_render(client, experiment_id, plate_id, wave_number)
        if plan["action"] != "render":
            return plan

        with tempfile.TemporaryDirectory() as work:
            # A constant name: the plate id is caller-supplied and would
            # otherwise reach the filesystem and the ffmpeg command line.
            video_path = os.path.join(work, "plate.mp4")
            written = encode_plate_video(client, plan["frames"], video_path)
            recorded = publish_plate_video(
                client,
                plan["key"],
                video_path,
                experiment_id=experiment_id,
                plate_id=plate_id,
                wave_number=wave_number,
                frame_count=written,
            )

    return {**plan, "action": "rendered", "recorded": recorded}
