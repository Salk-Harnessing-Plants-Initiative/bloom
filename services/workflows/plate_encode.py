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
from plate_video_path import (
    GRAVISCAN_IMAGES_BUCKET,
    GRAVISCAN_VIDEOS_BUCKET,
    plate_video_path,
)
from video_writer import ENCODE_TIMEOUT_SECONDS, VideoWriter

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

# What one frame may cost to decode. Bytes, not pixels: 60 Mpx costs 369 MB as
# 8-bit and 819 MB as 16-bit, so a pixel ceiling admitting the first admits the
# second. Sits above a 1600 dpi plate (369 MB) and below a 16-bit one at full
# size (487 MB), four of which do not fit 2g.
MAX_FRAME_DECODED_BYTES = 450 * 1024**2

# Peak RSS per source pixel through prepare_frame, measured on 4960x6850 and
# 6613x9133: 6.4-7.1 for 8-bit, 14.3-15.0 for 16-bit.
DEEP_BYTES_PER_PIXEL = 15
SHALLOW_BYTES_PER_PIXEL = 7

# A bound on one frame's bytes, not a claim about the record of them. The
# whole-plate guard in plate_video sums gravi_images.file_size_bytes, which is
# what the desktop wrote at upload — fine as a record, and not something to bet
# memory on: an interrupted upload, a resumed transfer or an app bug all leave
# it disagreeing with the object, and this path holds whatever arrives.
#
# A real frame is ~59 MB nominal and 93 MB for a detailed 16-bit scan, so this
# refuses nothing the scanners produce. The bucket's own cap is 500 MB.
MAX_FRAME_BYTES = 256 * 1024**2

# The modes carrying more than 8 bits per channel, and the full scale they are
# reduced from. `F` is absent deliberately — see `_to_8bit_rgb`.
DEEP_MODES = ("I;16", "I;16B", "I;16L", "I")
DEEP_FULL_SCALE = 65535
# Half scale. Below this a frame reduces to under half the output range, which
# on a 12-bit source is 15 of 255 — unreadable. A plate is lit from behind and
# its background sits near saturation, so a real full-scale frame clears this
# comfortably; one that does not is a source this reduction does not fit.
DEEP_MIN_PEAK = DEEP_FULL_SCALE // 2

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


def decoded_bytes(width: int, height: int, mode: str) -> int:
    """Roughly what this frame will cost in memory, from its header alone."""
    per_pixel = (
        DEEP_BYTES_PER_PIXEL if mode in DEEP_MODES else SHALLOW_BYTES_PER_PIXEL
    )
    return width * height * per_pixel


def prepare_frame(image_bytes: bytes, label: str) -> np.ndarray:
    """One stored image as a labelled, encodable frame.

    Returns 8-bit RGB, `PLATE_VIDEO_WIDTH` wide, with the label band beneath.
    """
    with Image.open(io.BytesIO(image_bytes)) as image:
        # Before load(), which is what builds the picture in memory. open() has
        # read only the header, which carries both halves of the cost.
        cost = decoded_bytes(image.width, image.height, image.mode)
        if cost > MAX_FRAME_DECODED_BYTES:
            raise FrameTooLarge(
                f"{image.width}x{image.height} {image.mode} needs about "
                f"{cost // 1024**2} MB to decode, past the "
                f"{MAX_FRAME_DECODED_BYTES // 1024**2} MB one render may hold"
            )
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
        raise FrameDepthUnsupported(
            f"a {image.mode} frame carries no fixed full scale, so it cannot be "
            "reduced to 8 bits without inventing one"
        )

    if image.mode in DEEP_MODES:
        data = np.asarray(image)
        # Measured for every deep mode, not only the wider ones. DEEP_FULL_SCALE
        # is an assumption about the source, and a frame that does not match it
        # is reduced into a picture nobody can read — dark if the source is
        # narrower, white if it is wider. Checking costs one pass over an array
        # the decode has already paid for.
        peak = int(data.max()) if data.size else 0
        if peak > DEEP_FULL_SCALE:
            raise FrameDepthUnsupported(
                f"a {image.mode} frame peaks at {peak}, past the "
                f"{DEEP_FULL_SCALE} full scale this reduces from — every "
                "pixel above it would come out white"
            )
        if peak < DEEP_MIN_PEAK:
            # Warned, not refused. A 12-bit sensor in a 16-bit container tops
            # out at 4095 and reduces to 15 of 255 — a black video with a
            # legible timestamp — but refusing on peak would make acceptance
            # depend on frame content, and a dim frame must reduce the same way
            # a bright one does or the run is not comparable frame to frame.
            # So: say so once, loudly enough that "the video is black" is one
            # grep rather than a bisect, and leave the scale alone.
            logger.warning(
                "a %s frame peaks at %d, far short of the %d full scale it is "
                "reduced from — it will render at %d/255. The source is "
                "probably not full-scale 16-bit.",
                image.mode, peak, DEEP_FULL_SCALE, peak >> 8,
            )
        if data.dtype != np.uint16:
            # A negative value wraps round to a bright pixel on the way to
            # uint8. Clipping a uint16 frame cannot change it, and would cost a
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
    """A frame could not be fetched or decoded, and the render must not go on.

    `path` is the object it happened to, carried separately from the message so
    a caller can be told which frame without being told why. The why is the
    storage client's own error, which names the internal gateway, the database
    role and PostgREST's codes — an operator's information, not a caller's.
    """

    def __init__(self, message: str, path: str | None = None):
        super().__init__(message)
        self.path = path


class FrameDepthUnsupported(FrameUnreadable):
    """The image is intact; its bit depth has no defined reduction to 8 bits.

    Its own type because the operator action differs: "could not decode" reads
    as a corrupt file and sends someone to rescan the plate, when the file is
    fine and it is the scanner's output depth this encoder does not cover.
    Deliberately not a ValueError — the frame loop reads that as a size
    mismatch and would report it as "does not match the rest of the plate".
    """


class FrameTooLarge(FrameUnreadable):
    """The frame is intact; it is too big for one render to hold.

    Its own type because "could not be read" sends someone to rescan a plate
    that scanned correctly — the same reason FrameDepthUnsupported exists.
    """


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
    # Opt in to the stall deadline. The cyl path does not, so its behaviour is
    # unchanged: it is shipped, and a ceiling it never asked for would surface
    # as dozens of "skipping frame" warnings before the real failure.
    writer = VideoWriter(
        filename=out_path, fps=PLATE_FPS, deadline=ENCODE_TIMEOUT_SECONDS
    )
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
        raise FrameUnreadable(f"could not download {path}: {exc}", path) from exc

    if not data:
        raise FrameUnreadable(f"{path} is empty", path)

    if len(data) > MAX_FRAME_BYTES:
        raise FrameTooLarge(
            f"{path} is {len(data) / 1024**2:.0f} MB, past the "
            f"{MAX_FRAME_BYTES // 1024**2} MB a plate frame can be",
            path,
        )

    try:
        return prepare_frame(data, label)
    except FrameDepthUnsupported as exc:
        # Intact, just not reducible. Named, but not called a decode failure.
        raise FrameDepthUnsupported(f"{path}: {exc}", path) from exc
    except FrameTooLarge as exc:
        raise FrameTooLarge(f"{path}: {exc}", path) from exc
    except Exception as exc:
        raise FrameUnreadable(f"could not decode {path}: {exc}", path) from exc


# --- publishing --------------------------------------------------------------


class NotRecorded(RuntimeError):
    """The video is stored but its row was not written.

    `key` is the object it concerns, carried separately from the message for
    the reason `FrameUnreadable.path` is: the message wraps the database
    client's own error, which names the role and PostgREST's SQLSTATEs, and the
    caller is not the audience for either.
    """

    def __init__(self, message: str, key: str | None = None):
        super().__init__(message)
        self.key = key


class PlateMismatch(RuntimeError):
    """The destination does not name the plate being recorded; nothing stored.

    Its own type, and deliberately not a `NotRecorded`: that one means the
    video reached storage and only the row is missing, which the next request
    repairs. This is the opposite — the run stopped before writing anything,
    on purpose, because storing it would have overwritten a different plate's
    video with this one. Nothing to repair, and nothing to retry: the caller
    passed a pair that cannot both be right.
    """


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
    # The key and the identity arrive separately, so nothing but this stops a
    # caller crossing them — and a crossed pair is invisible afterwards: the
    # object is upserted, so plate A's video silently replaces plate B's, and
    # the row files it under A. Plate ids repeat across waves by design, so
    # nobody looks at a video and thinks it belongs to a different plate.
    expected = plate_video_path(experiment_id, wave_number, plate_id)
    if expected is None:
        raise PlateMismatch(
            f"experiment {experiment_id}, plate {plate_id!r}, wave "
            f"{wave_number} does not name a plate a video can be stored for"
        )
    if key != expected:
        raise PlateMismatch(
            f"refusing to store {key}: experiment {experiment_id}, plate "
            f"{plate_id!r}, wave {wave_number} belongs at {expected}"
        )

    with open(video_path, "rb") as handle:
        video = handle.read()

    if not video:
        raise NotRecorded(f"the encoder produced an empty file at {video_path}", key)

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
        raise NotRecorded(f"{key} was stored but recording it failed: {exc}", key) from exc

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

    # The plate's own lock first: with every slot taken and this plate one of
    # the four rendering, "already being rendered" is the true answer and
    # "the encoder is busy" is not. Neither acquire waits, so ordering them
    # costs nothing.
    with plate_lock(plan["key"]), encode_slot():
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
