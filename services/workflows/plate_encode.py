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
import threading
from contextlib import contextmanager

import numpy as np
from PIL import Image

from plate_timelapse import PLATE_FPS, annotate, label_for
from plate_video import first_capture
from plate_video_path import GRAVISCAN_IMAGES_BUCKET, GRAVISCAN_VIDEOS_BUCKET
from video_writer import VideoWriter

logger = logging.getLogger(__name__)

# Wide enough to make out individual roots — 720 was checked against a real
# plate and they are not distinguishable. A whole run is about 3.5 MB at this
# width, so file size does not constrain the choice; browser decode limits and
# legibility do.
PLATE_VIDEO_WIDTH = 1440

# How many plates may encode at once. Chiefly so forty simultaneous clicks do
# not saturate the link to storage and make every other request slower — but it
# is also the multiplier on a render's memory, so it is where a container limit
# has to be read from. tracemalloc around `prepare_frame` on a 4960x6850
# source: 18 MB for an 8-bit frame, 170 MB for 16-bit. Four together is under
# 700 MB, plus Pillow's own decode buffer, which is allocated outside the
# Python allocator and so is not in those figures. Reducing in integer
# arithmetic is what holds the 16-bit number down; a full-resolution float copy
# of the source made the same measurement 578 MB.
MAX_CONCURRENT_ENCODES = 4

# The modes carrying more than 8 bits per channel, and the full scale they are
# reduced from. `F` is absent deliberately — see `_to_8bit_rgb`.
DEEP_MODES = ("I;16", "I;16B", "I;16L", "I")
DEEP_FULL_SCALE = 65535

# Nothing here bounds a single download. The storage client takes no per-call
# timeout, so that belongs on its HTTP client rather than in this module — the
# cyl path has the same gap. A whole plate is deliberately unbounded either
# way: 86 frames measured at 0.68s each, and a longer run should take longer
# rather than be truncated.

# BoundedSemaphore rather than Semaphore: a release that was never acquired is
# a capacity leak in the direction nothing else would notice, and this raises on
# it instead of quietly granting a fifth slot.
_encode_slots = threading.BoundedSemaphore(MAX_CONCURRENT_ENCODES)
# Per process, so it serialises only within one worker. The object key is
# derived from the plate and carries no version, so two workers would race to
# write the same one; docker-compose.prod.yml pins this service to a single
# uvicorn worker for that reason, and the encode semaphore does not substitute
# for it — it bounds how many renders run, not which key they write.
_plate_locks: dict[str, threading.Lock] = {}
# `dict.setdefault` is atomic under the GIL, so this guard is redundant on
# CPython today and is not on a free-threaded build. Two threads each creating
# a lock for one key is not a race that shows up as an error — both proceed,
# and two encodes overwrite one object key.
_plate_locks_guard = threading.Lock()


@contextmanager
def plate_lock(key: str, timeout: float | None = None):
    """Exclusive use of one plate's video, or a refusal.

    Keyed by the object key, so two requests for the same plate serialise while
    different plates do not. The table stays bounded by real plates because the
    caller takes this once the plate is known to exist.

    Refuses by default, for the reason `encode_slot` does: the caller is a
    synchronous request with a client already waiting, a whole plate is about a
    minute, and `acquire` cannot be interrupted — so a queued thread outlives
    the client's timeout and goes on holding an encode slot for a video nobody
    is waiting for any more.
    """
    with _plate_locks_guard:
        lock = _plate_locks.setdefault(key, threading.Lock())

    # A timeout is only legal on a blocking acquire, so the default asks for
    # the lock once and takes the answer.
    waited = -1 if timeout is None else timeout
    if not lock.acquire(blocking=timeout is not None, timeout=waited):
        raise PlateBusy(f"{key} is already being rendered; try again shortly")
    try:
        yield
    finally:
        lock.release()


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

    A deeper source is scaled from a fixed full scale rather than converted
    directly: Pillow's `convert("RGB")` clamps, so a 16-bit frame would come
    back as a field of 255 wherever it exceeded 8 bits.

    The divisor is the same for every frame. Scaling each frame by its own
    maximum makes brightness track the brightest pixel in that frame, so one
    dust speck or hot pixel darkens everything else — and over ~86 frames the
    video strobes with the noise instead of showing the plant. Fixed scaling
    is comparable frame to frame, which is what a measurement needs.

    A shift rather than a multiply, and never a float copy of the source: at
    4960x6850 a float64 intermediate is 272 MB and there are two of them.
    """
    if image.mode == "F":
        # No defined full scale to divide by. Refusing says so; picking one
        # would silently rescale a frame by a number nothing chose.
        raise ValueError(
            f"a {image.mode} frame carries no fixed full scale, so it cannot be "
            "reduced to 8 bits without inventing one"
        )

    if image.mode in DEEP_MODES:
        data = np.asarray(image)
        if data.dtype != np.uint16:
            # Only the signed and wider modes need this: mode I is int32, and a
            # negative value wraps round to a bright pixel on the way to uint8.
            # Clipping a uint16 frame cannot change it, and would cost a
            # full-resolution copy to say so.
            data = np.clip(data, 0, DEEP_FULL_SCALE).astype(np.uint16)
        image = Image.fromarray((data >> 8).astype(np.uint8), mode="L")

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


class PlateBusy(EncoderBusy):
    """This plate is already being rendered. Also a reason to come back, and a
    subclass so a caller that answers one of these answers both."""


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
            try:
                writer.add(_fetch_frame(images, path, label))
            except ValueError as exc:
                # The writer refuses a frame that would shear every frame after
                # it. Two frames of one plate genuinely can differ — anything at
                # or below the target width keeps its own evened width, so 1439
                # and 1440 both arrive as themselves. That is this plate's data,
                # not a fault in the encoder, so it reads as one.
                raise FrameUnreadable(
                    f"{path} does not match the rest of the plate: {exc}"
                ) from exc
            written += 1
        writer.close()
    except Exception as failure:
        cleanup = _tear_down(writer, out_path)
        if cleanup is not None and isinstance(failure, BrokenPipeError):
            # ffmpeg died first and `failure` is only the symptom of writing to
            # its closed stdin. The cleanup carries the reason — a full disk,
            # say — so it is the one worth raising.
            raise cleanup from failure
        if cleanup is not None:
            logger.warning("encoder cleanup after a failed render: %s", cleanup)
        raise

    logger.info("encoded %s frames to %s", written, out_path)
    return written


def _tear_down(writer: VideoWriter, out_path: str) -> Exception | None:
    """Close the encoder and delete its output. Returns why closing failed.

    The partial file goes because it is a playable MP4 of however many frames
    got through, indistinguishable from a complete one to anything downstream —
    and because this container's filesystem is a 512 MB tmpfs, so a run of
    failures would fill it and then every encode fails.
    """
    closing = None
    try:
        writer.close()
    except Exception as exc:
        closing = exc

    try:
        os.remove(out_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("could not remove the partial video at %s: %s", out_path, exc)

    return closing


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
