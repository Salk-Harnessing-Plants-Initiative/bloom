"""One stored image, turned into one frame of a plate time-lapse.

Real image bytes throughout — the thing under test is a decode, a resize and a
label, and a fake would only prove the calls were made in some order.
"""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

import plate_encode as pe
from plate_timelapse import LABEL_BAND_HEIGHT, PLATE_FPS

LABEL = "2026-03-06 21:36 PST\n+01h 10m"


def _png(width, height, mode="RGB") -> bytes:
    """A patterned image, so a transpose or a flip is visible in the pixels."""
    bands = len(Image.new(mode, (1, 1)).getbands())
    rows = np.arange(height, dtype=np.uint16)[:, None, None]
    cols = np.arange(width, dtype=np.uint16)[None, :, None]
    chans = np.arange(bands, dtype=np.uint16)[None, None, :]
    data = ((rows * 37 + cols * 11 + chans * 5) % 251).astype(np.uint8)

    buf = io.BytesIO()
    Image.fromarray(data.squeeze() if bands == 1 else data, mode).save(buf, "PNG")
    return buf.getvalue()


def test_a_native_sized_plate_comes_back_at_the_target_width():
    frame = pe.prepare_frame(_png(4960, 6850), LABEL)
    assert frame.shape[1] == pe.PLATE_VIDEO_WIDTH


def test_the_aspect_ratio_is_kept():
    """4960x6850 is portrait; stretching it would distort every root.

    The picture height is read off the frame, not computed from the ratio —
    deriving it from the same arithmetic the code uses asserts nothing.
    """
    frame = pe.prepare_frame(_png(4960, 6850), LABEL)
    picture_height = frame.shape[0] - LABEL_BAND_HEIGHT

    assert abs(picture_height / frame.shape[1] - 6850 / 4960) < 0.01, (
        f"got {frame.shape[1]}x{picture_height}, which is not the source ratio"
    )


def test_the_label_is_added_after_the_downscale():
    """The band is a fixed height, so labelling first and shrinking after would
    scale the text down with the picture until it is unreadable."""
    frame = pe.prepare_frame(_png(4960, 6850), LABEL)
    picture_height = round(6850 * pe.PLATE_VIDEO_WIDTH / 4960)
    picture_height -= picture_height % 2

    band = frame[picture_height:]
    assert band.shape[0] >= 40, "the label band was shrunk with the picture"
    assert band.max() > 128, "no text was drawn in the band"


def test_the_result_is_8_bit_rgb():
    """`annotate` refuses anything else, and ffmpeg is told rgb24."""
    frame = pe.prepare_frame(_png(400, 600), LABEL)
    assert frame.dtype == np.uint8
    assert frame.ndim == 3 and frame.shape[2] == 3


@pytest.mark.parametrize("mode", ["RGB", "L", "RGBA", "P"])
def test_every_mode_the_scanners_might_write_becomes_rgb(mode):
    frame = pe.prepare_frame(_png(400, 600, mode=mode), LABEL)
    assert frame.shape[2] == 3


def test_a_16_bit_source_is_scaled_rather_than_clamped():
    """`convert("RGB")` clamps, so a 16-bit plate would come back as a white
    field wherever it exceeded 255 — the highlights, which is the tissue."""
    data = np.linspace(0, 65535, 400 * 600, dtype=np.uint16).reshape(600, 400)
    buf = io.BytesIO()
    Image.fromarray(data).save(buf, "PNG")

    frame = pe.prepare_frame(buf.getvalue(), LABEL)
    picture = frame[:600]
    assert picture.max() > 200, "the bright end was lost"
    assert picture.min() < 40, "the dark end was lost"
    assert 40 < np.median(picture) < 215, (
        "a clamped conversion leaves a bimodal frame, not a gradient"
    )


def test_a_plate_narrower_than_the_target_is_not_enlarged():
    """Interpolating up invents detail; the video is better small and honest."""
    frame = pe.prepare_frame(_png(600, 800), LABEL)
    assert frame.shape[1] == 600


def test_both_dimensions_come_back_even():
    """H.264 needs even dimensions, and VideoWriter pads an odd frame by
    repeating its edge — which would land on the label."""
    frame = pe.prepare_frame(_png(601, 803), LABEL)
    assert frame.shape[1] % 2 == 0
    assert frame.shape[0] % 2 == 0


def test_the_picture_is_not_drawn_over():
    """The band is added beneath, so every source pixel survives the round trip
    that a scientist measures from."""
    source = _png(400, 600)
    frame = pe.prepare_frame(source, LABEL)
    expected = np.asarray(Image.open(io.BytesIO(source)), dtype=np.uint8)
    assert np.array_equal(frame[:600], expected)


def test_a_different_label_changes_only_the_band():
    a = pe.prepare_frame(_png(400, 600), "2026-03-06 21:36 PST\n+01h 10m")
    b = pe.prepare_frame(_png(400, 600), "2026-03-07 09:00 PST\n+12h 34m")
    assert np.array_equal(a[:600], b[:600]), "the picture changed with the label"
    assert not np.array_equal(a[600:], b[600:]), "the band did not change"


# --- many frames into a video ------------------------------------------------
#
# A fake ffmpeg stands in for the encoder; the frames are real image bytes.

import subprocess  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

T0 = datetime(2026, 3, 6, 20, 26, tzinfo=timezone.utc)


class _Stdin:
    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(data)

    def close(self):
        pass


class _Ffmpeg:
    spawned: list = []

    def __init__(self, cmd, **_kw):
        self.cmd = cmd
        self.stdin = _Stdin()
        self.stderr = type("_S", (), {"read": lambda s: b"", "close": lambda s: None})()
        self.returncode = 0
        self.waited = False
        _Ffmpeg.spawned.append(self)

    def wait(self, timeout=None):
        self.waited = True
        with open(self.cmd[-1], "wb") as fh:
            fh.write(b"\x00\x01")
        return 0

    def kill(self):
        pass


class _Images:
    """The graviscan-images bucket: one payload per object path."""

    def __init__(self, by_path, raises=None):
        self._by_path = by_path
        self._raises = raises
        self.downloaded: list[str] = []

    def download(self, path):
        self.downloaded.append(path)
        if self._raises is not None:
            raise self._raises
        return self._by_path.get(path)


class _EncodeClient:
    def __init__(self, by_path, raises=None):
        self.images = _Images(by_path, raises)

    @property
    def storage(self):
        outer = self

        class _S:
            def from_(self, name):
                outer.bucket = name
                return outer.images

        return _S()


@pytest.fixture
def ffmpeg(monkeypatch):
    spawned: list = []
    monkeypatch.setattr(_Ffmpeg, "spawned", spawned)
    monkeypatch.setattr(subprocess, "Popen", _Ffmpeg)
    import video_writer

    monkeypatch.setattr(video_writer.subprocess, "Popen", _Ffmpeg)
    return spawned


def _frames(n, width=400, height=600):
    return [
        {"object_path": f"12/wave-1/P7_{i}.tif", "capture_date": T0 + timedelta(minutes=7 * i)}
        for i in range(n)
    ]


def _payloads(frames, width=400, height=600):
    return {f["object_path"]: _png(width, height) for f in frames}


def test_every_frame_reaches_the_encoder(ffmpeg, tmp_path):
    frames = _frames(4)
    client = _EncodeClient(_payloads(frames))
    out = str(tmp_path / "out.mp4")

    assert pe.encode_plate_video(client, frames, out) == 4
    assert len(ffmpeg[0].stdin.chunks) == 4


def test_the_frames_are_downloaded_in_the_order_given(ffmpeg, tmp_path):
    """The list is already sorted by capture time; encoding out of order would
    make the video play backwards in places."""
    frames = _frames(3)
    client = _EncodeClient(_payloads(frames))
    pe.encode_plate_video(client, frames, str(tmp_path / "out.mp4"))

    assert client.images.downloaded == [f["object_path"] for f in frames]


def test_the_video_is_encoded_at_the_plate_frame_rate(ffmpeg, tmp_path):
    """The cyl call site never passes fps and inherits 30, which at one frame
    per seven minutes would be a blur."""
    frames = _frames(2)
    pe.encode_plate_video(_EncodeClient(_payloads(frames)), frames, str(tmp_path / "o.mp4"))

    cmd = ffmpeg[0].cmd
    assert cmd[cmd.index("-r") + 1] == str(PLATE_FPS)


def test_the_frames_come_from_the_images_bucket(ffmpeg, tmp_path):
    frames = _frames(1)
    client = _EncodeClient(_payloads(frames))
    pe.encode_plate_video(client, frames, str(tmp_path / "o.mp4"))
    assert client.bucket == "graviscan-images"


def test_an_undownloadable_frame_fails_the_render_naming_it(ffmpeg, tmp_path):
    """`video.py` skips and logs. For a growth series the dropped frame may be
    exactly where the root moved, and the video still looks complete."""
    frames = _frames(3)
    client = _EncodeClient({}, raises=Exception("connection reset"))

    with pytest.raises(pe.FrameUnreadable) as ei:
        pe.encode_plate_video(client, frames, str(tmp_path / "o.mp4"))
    assert "P7_0.tif" in str(ei.value)


def test_an_empty_object_fails_rather_than_encoding_nothing(ffmpeg, tmp_path):
    frames = _frames(2)
    payloads = _payloads(frames)
    payloads[frames[1]["object_path"]] = b""

    with pytest.raises(pe.FrameUnreadable) as ei:
        pe.encode_plate_video(_EncodeClient(payloads), frames, str(tmp_path / "o.mp4"))
    assert "P7_1.tif" in str(ei.value)
    assert "empty" in str(ei.value), (
        "an empty object should say so, not surface as a decode failure"
    )


def test_an_undecodable_frame_fails_naming_it(ffmpeg, tmp_path):
    frames = _frames(2)
    payloads = _payloads(frames)
    payloads[frames[1]["object_path"]] = b"not an image"

    with pytest.raises(pe.FrameUnreadable) as ei:
        pe.encode_plate_video(_EncodeClient(payloads), frames, str(tmp_path / "o.mp4"))
    assert "P7_1.tif" in str(ei.value)
    assert "decode" in str(ei.value)


def test_a_failed_render_still_tears_the_encoder_down(ffmpeg, tmp_path):
    """An abandoned ffmpeg holds a pipe and a process for the life of the worker."""
    frames = _frames(2)
    payloads = _payloads(frames)
    payloads[frames[1]["object_path"]] = b"not an image"

    with pytest.raises(pe.FrameUnreadable):
        pe.encode_plate_video(_EncodeClient(payloads), frames, str(tmp_path / "o.mp4"))

    assert ffmpeg[0].waited, "ffmpeg was abandoned rather than closed"


def test_no_frames_is_a_failure_not_an_empty_video(ffmpeg, tmp_path):
    with pytest.raises(pe.FrameUnreadable):
        pe.encode_plate_video(_EncodeClient({}), [], str(tmp_path / "o.mp4"))
    assert ffmpeg == [], "ffmpeg was started for a plate with no frames"


def test_each_frame_carries_its_own_elapsed_label(ffmpeg, tmp_path):
    """The label counts from the first capture, so every frame differs."""
    frames = _frames(3)
    pe.encode_plate_video(_EncodeClient(_payloads(frames)), frames, str(tmp_path / "o.mp4"))

    chunks = ffmpeg[0].stdin.chunks
    assert len(set(chunks)) == 3, "two frames carried the same label"


# --- bounded -----------------------------------------------------------------
#
# Not a memory bound: the host has 633 GB free and a render peaks around
# 194 MB. The semaphore is so forty simultaneous clicks do not saturate the
# link to storage; the lock is so two requests for one plate do not both encode.

import threading  # noqa: E402


def test_one_plate_gets_one_lock():
    assert pe.plate_lock("12/wave-1/P7.mp4") is pe.plate_lock("12/wave-1/P7.mp4")


def test_different_plates_get_different_locks():
    """A shared lock would serialise every render in the service."""
    assert pe.plate_lock("12/wave-1/P7.mp4") is not pe.plate_lock("12/wave-1/P8.mp4")


def test_the_lock_actually_excludes():
    lock = pe.plate_lock("12/wave-1/held.mp4")
    with lock:
        assert lock.acquire(blocking=False) is False
    assert lock.acquire(blocking=False) is True
    lock.release()


def test_the_lock_table_is_built_under_its_own_guard():
    """Two threads asking at once must not each create a lock — one of them
    would then hold a lock nobody else respects."""
    seen = []
    barrier = threading.Barrier(8)

    def grab():
        barrier.wait()
        seen.append(pe.plate_lock("12/wave-1/raced.mp4"))

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(lock) for lock in seen}) == 1


def test_a_slot_is_released_when_the_render_finishes():
    for _ in range(pe.MAX_CONCURRENT_ENCODES + 2):
        with pe.encode_slot():
            pass


def test_a_slot_is_released_when_the_render_raises():
    """A slot leaked on the error path takes a permanent bite out of capacity."""
    for _ in range(pe.MAX_CONCURRENT_ENCODES + 2):
        with pytest.raises(RuntimeError):
            with pe.encode_slot():
                raise RuntimeError("render failed")


def test_asking_past_the_limit_refuses_rather_than_queueing():
    """The caller is a synchronous request with a client already waiting;
    queueing would hold a slot's memory to achieve nothing it can see."""
    held = [pe.encode_slot() for _ in range(pe.MAX_CONCURRENT_ENCODES)]
    for slot in held:
        slot.__enter__()
    try:
        with pytest.raises(pe.EncoderBusy) as ei:
            with pe.encode_slot():
                pass
        assert str(pe.MAX_CONCURRENT_ENCODES) in str(ei.value)
    finally:
        for slot in held:
            slot.__exit__(None, None, None)


def test_a_caller_may_wait_for_a_slot_if_it_chooses():
    """`timeout` is opt-in: a worker with no client waiting can queue."""
    held = [pe.encode_slot() for _ in range(pe.MAX_CONCURRENT_ENCODES)]
    for slot in held:
        slot.__enter__()

    released = threading.Event()

    def free_one():
        released.wait(1)
        held[0].__exit__(None, None, None)

    freer = threading.Thread(target=free_one)
    freer.start()
    released.set()

    try:
        with pe.encode_slot(timeout=2):
            pass
    finally:
        freer.join()
        for slot in held[1:]:
            slot.__exit__(None, None, None)
