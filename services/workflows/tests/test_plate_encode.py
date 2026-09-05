"""One stored image, turned into one frame of a plate time-lapse.

Real image bytes throughout — the thing under test is a decode, a resize and a
label, and a fake would only prove the calls were made in some order.
"""

from __future__ import annotations

import io
import os
from contextlib import contextmanager

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


def _png16(data: np.ndarray) -> bytes:
    """A 16-bit grayscale PNG carrying exactly `data`."""
    buf = io.BytesIO()
    Image.fromarray(data.astype(np.uint16)).save(buf, "PNG")
    return buf.getvalue()


def _deep(mode, data: np.ndarray) -> Image.Image:
    """A decoded image in one of the deeper modes.

    Built rather than round-tripped through a file: PNG cannot carry `F` at
    all, and reads `I;16B` and `I;16L` back as plain `I;16` — so a file would
    silently test the same branch three times. What arrives here is a decoded
    image either way.
    """
    if mode == "I":
        return Image.fromarray(data.astype(np.int32), mode="I")
    if mode == "I;16":
        return Image.fromarray(data.astype(np.uint16))
    order = ">u2" if mode == "I;16B" else "<u2"
    size = (data.shape[1], data.shape[0])
    return Image.frombytes(mode, size, data.astype(order).tobytes())


def _stripes(width, height) -> bytes:
    """One-pixel black and white columns — the finest detail a plate can hold,
    and what a root looks like once the picture is 3.4x too small for it."""
    cols = (np.arange(width) % 2 * 255).astype(np.uint8)
    data = np.repeat(cols[None, :, None], height, axis=0).repeat(3, axis=2)
    buf = io.BytesIO()
    Image.fromarray(data, "RGB").save(buf, "PNG")
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
    """ffmpeg is told rgb24 and reads raw frames by byte count, so a frame with
    a fourth channel is not rejected — it shears every frame after it."""
    frame = pe.prepare_frame(_png(400, 600, mode=mode), LABEL)
    assert frame.shape[2] == 3
    assert frame.dtype == np.uint8


@pytest.mark.parametrize("mode", ["CMYK", "YCbCr"])
def test_a_colour_mode_that_is_not_rgb_is_converted_not_reinterpreted(mode):
    """Both are three or four 8-bit channels that are not red, green and blue.
    Reinterpreting rather than converting gives a frame of plausible size and
    wrong colours. Built rather than round-tripped, because PNG cannot carry
    either one — what arrives here is a decoded image regardless."""
    source = Image.new(mode, (40, 60), "red" if mode == "CMYK" else None)
    converted = np.asarray(pe._to_8bit_rgb(source))

    assert converted.shape == (60, 40, 3)
    assert converted.dtype == np.uint8


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


def test_a_hot_pixel_does_not_darken_the_rest_of_the_frame():
    """Scaling a 16-bit frame by its own maximum makes brightness track the
    brightest pixel in it. One dust speck, specular highlight or sensor hot
    pixel then rescales every other pixel, and over ~86 frames the video
    strobes with the noise rather than showing the plant — while a scientist
    measures root movement off exactly that brightness."""
    plain = np.full((600, 400), 4096, dtype=np.uint16)
    speckled = plain.copy()
    speckled[0, 0] = 65535

    without = pe.prepare_frame(_png16(plain), LABEL)[:600]
    with_speck = pe.prepare_frame(_png16(speckled), LABEL)[:600]

    assert np.array_equal(without[1:], with_speck[1:]), (
        "one bright pixel changed the brightness of every other pixel"
    )


def test_the_same_value_reduces_the_same_way_in_every_frame():
    """The transfer function is fixed, so two frames of the same tissue at
    different exposures stay comparable rather than being normalised apart."""
    dim = pe.prepare_frame(_png16(np.full((600, 400), 8192, np.uint16)), LABEL)
    bright = np.full((600, 400), 8192, np.uint16)
    bright[:, :100] = 60000

    assert np.array_equal(
        dim[:600, 200:300], pe.prepare_frame(_png16(bright), LABEL)[:600, 200:300]
    )


@pytest.mark.parametrize("mode", ["I;16", "I;16B", "I;16L", "I"])
def test_every_deep_mode_is_reduced_rather_than_clamped(mode):
    """All four are listed as deep; only one of them had a test, so three could
    have been dropped from the list without anything noticing. Written out
    rather than read from `DEEP_MODES`, which would shrink along with it."""
    assert mode in pe.DEEP_MODES, f"{mode} is no longer reduced, only clamped"

    ramp = np.linspace(0, 65535, 400 * 600, dtype=np.uint16).reshape(600, 400)
    picture = np.asarray(pe._to_8bit_rgb(_deep(mode, ramp)))

    assert picture.max() > 200, f"{mode}: the bright end was lost"
    assert picture.min() < 40, f"{mode}: the dark end was lost"
    assert 40 < np.median(picture) < 215, (
        f"{mode}: a clamped conversion leaves a bimodal frame, not a gradient"
    )


def test_a_floating_point_frame_is_refused_rather_than_given_a_scale():
    """There is no full scale to divide by; picking one would rescale the frame
    by a number nothing chose, silently."""
    with pytest.raises(pe.FrameDepthUnsupported, match="full scale"):
        pe._to_8bit_rgb(Image.new("F", (40, 60)))


def test_a_negative_value_does_not_come_back_bright():
    """Mode I is signed, and -5 wraps round to 251 on the way to uint8."""
    picture = np.asarray(pe._to_8bit_rgb(_deep("I", np.full((60, 40), -5))))
    assert picture.max() == 0, f"a negative value rendered as {picture.max()}"


def test_a_deep_frame_reaches_the_reduction_through_the_decoder(): 
    """The parametrised cases build an image directly, so this one proves the
    real path — bytes off storage — still lands in the deep branch."""
    ramp = np.linspace(0, 65535, 400 * 600, dtype=np.uint16).reshape(600, 400)
    picture = pe.prepare_frame(_png16(ramp), LABEL)[:600]
    assert picture.max() > 200 and picture.min() < 40


def test_a_real_plate_size_comes_back_even():
    """4960x6850 scales to 1440x1989 — odd, and with the band 2033, also odd.
    601x803 takes the not-enlarged branch and never reaches the resize, so it
    proves nothing about the size a real plate actually is."""
    frame = pe.prepare_frame(_png(4960, 6850), LABEL)
    assert frame.shape[1] % 2 == 0, f"width {frame.shape[1]} is odd"
    assert frame.shape[0] % 2 == 0, f"height {frame.shape[0]} is odd"


def test_the_downscale_resamples_rather_than_picking_pixels():
    """Nearest-neighbour on a reduction aliases thin roots in and out between
    frames — the exact artefact this video exists to show. It samples source
    pixels, so on one-pixel stripes every output pixel is still pure black or
    pure white; resampling averages them and produces neither.

    The band is excluded by measuring it off the frame: the label is drawn with
    antialiased glyphs, so it carries greys of its own whatever the picture did.
    """
    frame = pe.prepare_frame(_stripes(2880, 600), LABEL)
    picture = frame[: frame.shape[0] - LABEL_BAND_HEIGHT]

    assert picture.size, "the picture was empty, so nothing was measured"
    assert set(np.unique(picture)) - {0, 255}, (
        "every pixel is a source pixel, so the stripes were sampled, not resampled"
    )


def test_a_degenerately_small_source_still_produces_a_frame():
    """Evening a 1px side by subtraction gives 0, and a zero-sized crop is not
    a frame ffmpeg can be opened with."""
    frame = pe.prepare_frame(_png(1, 1), LABEL)
    assert frame.shape[0] > LABEL_BAND_HEIGHT and frame.shape[1] >= 2
    assert frame.shape[0] % 2 == 0 and frame.shape[1] % 2 == 0


def _ink_rows(band: np.ndarray) -> list[int]:
    """The height of each run of rows carrying text, top to bottom."""
    lit = (band > 128).any(axis=(1, 2))
    runs, start = [], None
    for row, on in enumerate(lit):
        if on and start is None:
            start = row
        elif not on and start is not None:
            runs.append(row - start)
            start = None
    if start is not None:
        runs.append(len(lit) - start)
    return runs


def test_the_label_is_legible_at_the_size_the_video_is_watched_at():
    """The band is a fixed number of rows on a ~2068-row frame, and a player
    scales the whole thing down — so legibility is the ink height as a fraction
    of the frame, not the font size. The default face draws digits at about 70%
    of its em, so a 16px font was 11 rows: under 6px on a 1080-tall player,
    where the elapsed line is the first to go and it is the one that makes an
    irregular capture gap visible rather than silent."""
    frame = pe.prepare_frame(_png(4960, 6850), LABEL)
    lines = _ink_rows(frame[-LABEL_BAND_HEIGHT:])

    assert len(lines) == 2, f"expected two label lines, found {len(lines)}"

    on_a_1080_player = min(lines) / frame.shape[0] * 1080
    assert on_a_1080_player >= 10, (
        f"the digits render at {on_a_1080_player:.1f}px on a 1080-tall player"
    )


def test_the_label_still_fits_the_band_it_is_given():
    """Sizing the font up without the band would clip the second line, and the
    band is what keeps the video's dimensions constant."""
    frame = pe.prepare_frame(_png(4960, 6850), LABEL)
    band = frame[-LABEL_BAND_HEIGHT:]
    lit = (band > 128).any(axis=(1, 2))

    assert not lit[-1], "the label runs to the bottom edge, so it may be clipped"


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

from video_writer import VideoEncodeError  # noqa: E402

T0 = datetime(2026, 3, 6, 20, 26, tzinfo=timezone.utc)


class _Spawned(list):
    """The ffmpeg processes started, and how the next one is to behave.

    One object rather than two so a test can reach both: the encoder's failure
    paths are only visible when ffmpeg misbehaves, and the interesting ones are
    a pipe that breaks mid-stream and a non-zero exit that says why.
    """

    def __init__(self):
        super().__init__()
        self.stdin_fails_after = None  # frames accepted before the pipe breaks
        self.exit_code = 0
        self.stderr = b""


class _Stdin:
    def __init__(self, fails_after=None):
        self.chunks = []
        self._fails_after = fails_after

    def write(self, data):
        if self._fails_after is not None and len(self.chunks) >= self._fails_after:
            raise BrokenPipeError("ffmpeg is gone")
        self.chunks.append(data)

    def close(self):
        pass


class _Stderr:
    """Hands its payload over once, then reports EOF like a closed pipe."""

    def __init__(self, data=b""):
        self._data = data
        self.closed = False

    def read(self, size=-1):
        data, self._data = self._data, b""
        return data

    def close(self):
        self.closed = True


class _Ffmpeg:
    spawned: _Spawned = _Spawned()

    def __init__(self, cmd, **_kw):
        self.cmd = cmd
        self.stdin = _Stdin(_Ffmpeg.spawned.stdin_fails_after)
        self.stderr = _Stderr(_Ffmpeg.spawned.stderr)
        self.returncode = 0
        self.waited = False
        _Ffmpeg.spawned.append(self)

    def wait(self, timeout=None):
        self.waited = True
        with open(self.cmd[-1], "wb") as fh:
            fh.write(b"\x00\x01")
        self.returncode = _Ffmpeg.spawned.exit_code
        return self.returncode

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
    spawned = _Spawned()
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


def test_a_failed_render_leaves_no_partial_video(ffmpeg, tmp_path):
    """ffmpeg has already written a playable MP4 of the frames that got
    through, and nothing downstream can tell it from a complete one — the count
    that would describe it goes out with the exception. Leaving it also fills
    the container's 512MB tmpfs over a run of failures, after which every
    encode fails."""
    frames = _frames(5)
    payloads = _payloads(frames)
    payloads[frames[2]["object_path"]] = b"not an image"
    out = tmp_path / "o.mp4"

    with pytest.raises(pe.FrameUnreadable):
        pe.encode_plate_video(_EncodeClient(payloads), frames, str(out))

    assert not out.exists(), "a partial video was left where an upload could find it"


def test_a_successful_render_keeps_its_video(ffmpeg, tmp_path):
    """The clean-up is on the failure path only; deleting the output always
    would leave nothing to publish."""
    frames = _frames(2)
    out = tmp_path / "o.mp4"
    pe.encode_plate_video(_EncodeClient(_payloads(frames)), frames, str(out))
    assert out.exists()


def test_a_mismatched_frame_reads_as_this_plate_s_data(ffmpeg, tmp_path):
    """Anything at or below the target width keeps its own evened width, so a
    plate really can hold both 1438 and 1440. The writer refuses the second
    because it would shear every frame after it — but as a bare ValueError,
    which reaches a scientist as a 500 rather than as a bad plate."""
    frames = _frames(2)
    payloads = _payloads(frames)
    payloads[frames[1]["object_path"]] = _png(402, 600)

    with pytest.raises(pe.FrameUnreadable) as ei:
        pe.encode_plate_video(_EncodeClient(payloads), frames, str(tmp_path / "o.mp4"))
    assert "P7_1.tif" in str(ei.value)


def test_a_broken_pipe_gives_up_the_reason_ffmpeg_died(ffmpeg, tmp_path):
    """On a full disk ffmpeg exits saying so and the next add() raises
    BrokenPipeError — the symptom. Raising the symptom and logging the cause
    discards the only line that says what to fix."""
    frames = _frames(3)
    ffmpeg.stdin_fails_after = 1
    ffmpeg.exit_code = 1
    ffmpeg.stderr = b"av_interleaved_write_frame(): No space left on device"

    with pytest.raises(VideoEncodeError) as ei:
        pe.encode_plate_video(
            _EncodeClient(_payloads(frames)), frames, str(tmp_path / "o.mp4")
        )
    assert "No space left on device" in str(ei.value)


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
import time  # noqa: E402


def _off_thread(call, seconds=10.0):
    """Run `call` elsewhere and return what it raised, or None.

    A refusal that quietly became a wait does not fail an assertion, it blocks
    it — and a blocked assertion is a stuck CI job with no failure in it.
    """
    outcome = []
    done = threading.Event()

    def run():
        try:
            call()
            outcome.append(None)
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            outcome.append(exc)
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(seconds), "the call blocked instead of answering"
    return outcome[0]


def test_a_second_request_for_one_plate_is_refused_rather_than_queued():
    """A whole plate is about a minute, and `acquire` cannot be interrupted, so
    a queued thread outlives the client's timeout still holding an encode slot
    for a video nobody is waiting for."""
    key = "12/wave-1/held.mp4"

    def render_it_again():
        with pe.plate_lock(key):
            pass

    with pe.plate_lock(key):
        refusal = _off_thread(render_it_again)

    assert isinstance(refusal, pe.PlateBusy), f"got {refusal!r}"
    assert key in str(refusal)


def test_a_refused_plate_reads_as_busy_like_a_refused_slot():
    """Both mean come back shortly, and a caller that answers one answers both."""
    assert issubclass(pe.PlateBusy, pe.EncoderBusy)


def test_different_plates_do_not_block_each_other():
    """A shared lock would serialise every render in the service."""
    with pe.plate_lock("12/wave-1/P7.mp4"):
        with pe.plate_lock("12/wave-1/P8.mp4"):
            pass


def test_the_plate_is_released_when_the_render_finishes():
    for _ in range(3):
        with pe.plate_lock("12/wave-1/repeat.mp4"):
            pass


def test_the_plate_is_released_when_the_render_raises():
    """A plate left locked can never be rendered again while the worker lives."""
    for _ in range(3):
        with pytest.raises(RuntimeError):
            with pe.plate_lock("12/wave-1/raises.mp4"):
                raise RuntimeError("render failed")


def test_a_caller_may_wait_for_a_plate_if_it_chooses():
    """`timeout` is opt-in: a worker with no client waiting can queue."""
    key = "12/wave-1/waited.mp4"
    holder_in = threading.Event()
    release = threading.Event()

    def hold():
        with pe.plate_lock(key):
            holder_in.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    assert holder_in.wait(5)

    def free_it():
        release.set()

    threading.Timer(0.05, free_it).start()
    try:
        with pe.plate_lock(key, timeout=5):
            pass
    finally:
        release.set()
        holder.join(5)


def test_waiting_for_a_plate_gives_up_at_the_timeout():
    """A caller that asks to wait 200ms must not wait for the whole encode."""
    key = "12/wave-1/never-freed.mp4"
    with pe.plate_lock(key):
        started = time.monotonic()
        with pytest.raises(pe.PlateBusy):
            with pe.plate_lock(key, timeout=0.2):
                pass
        waited = time.monotonic() - started

    assert 0.15 <= waited < 3, f"waited {waited:.2f}s for a 0.2s timeout"


def test_racing_requests_for_one_plate_still_serialise():
    """Two threads each creating a lock for one key is not a race that raises:
    both proceed, and two encodes overwrite the same object key."""
    key = "12/wave-1/raced.mp4"
    barrier = threading.Barrier(8)
    inside = []
    peak = []

    def grab():
        barrier.wait()
        try:
            with pe.plate_lock(key, timeout=5):
                inside.append(1)
                peak.append(len(inside))
                time.sleep(0.01)
                inside.pop()
        except pe.PlateBusy:
            pass

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert peak and max(peak) == 1, f"{max(peak)} renders of one plate ran at once"


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
        # Off-thread for the same reason the plate-lock test is: if the refusal
        # regresses into a wait, asserting on this thread blocks forever and the
        # run dies on the CI timeout with no failing test to point at.
        def take_one_more():
            with pe.encode_slot():
                pass

        refusal = _off_thread(take_one_more)
        assert isinstance(refusal, pe.EncoderBusy), (
            f"asking past the limit must refuse, got {refusal!r}"
        )
        assert str(pe.MAX_CONCURRENT_ENCODES) in str(refusal)
    finally:
        for slot in held:
            slot.__exit__(None, None, None)


def test_the_encode_limit_is_the_number_the_service_is_sized_for():
    """Nothing else pins this: every other assertion reads the constant back.
    Four 16-bit plates peak under 700MB together, which is the number a
    container memory limit has to be read from."""
    assert pe.MAX_CONCURRENT_ENCODES == 4


def test_releasing_a_slot_that_was_never_taken_is_caught():
    """A plain Semaphore grants a fifth slot instead, and the leak runs the
    wrong way: capacity grows silently until storage is saturated."""
    with pytest.raises(ValueError):
        pe._encode_slots.release()


def test_waiting_for_a_slot_gives_up_at_the_timeout():
    """A caller asking to wait 200ms must not wait for a whole encode."""
    held = [pe.encode_slot() for _ in range(pe.MAX_CONCURRENT_ENCODES)]
    for slot in held:
        slot.__enter__()
    try:
        started = time.monotonic()
        with pytest.raises(pe.EncoderBusy):
            with pe.encode_slot(timeout=0.2):
                pass
        waited = time.monotonic() - started
        assert 0.15 <= waited < 3, f"waited {waited:.2f}s for a 0.2s timeout"
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


# --- publishing --------------------------------------------------------------
#
# Two systems, no shared transaction. Upload first, because the page reads the
# row without checking the object: a row without its file renders a broken
# player, a file without its row reads as no video yet.

import hashlib  # noqa: E402


class _Videos:
    def __init__(self, raises=None):
        self._raises = raises
        self.uploaded: list[tuple] = []

    def upload(self, key, data, options=None):
        if self._raises is not None:
            raise self._raises
        self.uploaded.append((key, data, options))


class _PublishClient:
    def __init__(self, upload_raises=None, rpc_raises=None):
        self.videos = _Videos(upload_raises)
        self._rpc_raises = rpc_raises
        self.calls: list[tuple] = []

    @property
    def storage(self):
        outer = self

        class _S:
            def from_(self, name):
                outer.bucket = name
                return outer.videos

        return _S()

    def rpc(self, name, params):
        self.calls.append((name, params))
        outer = self

        class _R:
            def execute(self):
                if outer._rpc_raises is not None:
                    raise outer._rpc_raises
                return type("_D", (), {"data": None})()

        return _R()


def _encoded(tmp_path, payload=b"\x00\x01mp4"):
    path = tmp_path / "out.mp4"
    path.write_bytes(payload)
    return str(path)


def _publish(client, tmp_path, frames=86, payload=b"\x00\x01mp4"):
    return pe.publish_plate_video(
        client,
        "12/wave-1/P7.mp4",
        _encoded(tmp_path, payload),
        experiment_id=12,
        plate_id="P7",
        wave_number=1,
        frame_count=frames,
    )


def test_the_video_is_uploaded_to_the_videos_bucket(tmp_path):
    client = _PublishClient()
    _publish(client, tmp_path)

    assert client.bucket == "graviscan-videos"
    key, data, options = client.videos.uploaded[0]
    assert key == "12/wave-1/P7.mp4"
    assert data == b"\x00\x01mp4"


def test_the_upload_overwrites_rather_than_failing_on_a_re_render(tmp_path):
    """The key is derived from the plate, so every render writes the same one."""
    client = _PublishClient()
    _publish(client, tmp_path)
    assert client.videos.uploaded[0][2]["upsert"] == "true"


def test_the_row_is_written_through_the_wrapper(tmp_path):
    """The role has no table grant; this is the only write it can make."""
    client = _PublishClient()
    _publish(client, tmp_path)

    name, params = client.calls[0]
    assert name == "record_gravi_plate_video"
    assert params["p_experiment_id"] == 12
    assert params["p_plate_id"] == "P7"
    assert params["p_wave_number"] == 1
    assert params["p_object_path"] == "12/wave-1/P7.mp4"


def test_the_recorded_duration_follows_the_frame_rate(tmp_path):
    """86 frames at 4 fps is 21.5 seconds, which rounds up — a duration short of
    the video would cut the last frames off a progress bar."""
    client = _PublishClient()
    _publish(client, tmp_path, frames=86)

    params = client.calls[0][1]
    assert params["p_fps"] == PLATE_FPS
    assert params["p_duration_seconds"] == 22


def test_the_recorded_size_and_hash_describe_the_file(tmp_path):
    client = _PublishClient()
    _publish(client, tmp_path, payload=b"some bytes")

    params = client.calls[0][1]
    assert params["p_file_size_bytes"] == len(b"some bytes")
    assert params["p_file_hash"] == hashlib.sha256(b"some bytes").hexdigest()


def test_the_upload_happens_before_the_row_is_written(tmp_path):
    """A row without its file renders a broken player; a file without its row
    reads as no video yet. Only one of those is worth showing a scientist."""
    client = _PublishClient(rpc_raises=Exception("db unavailable"))

    with pytest.raises(pe.NotRecorded):
        _publish(client, tmp_path)

    assert client.videos.uploaded, "the video was not stored before recording was attempted"


def test_a_failed_recording_raises_rather_than_reporting_success(tmp_path):
    """video.py logs and carries on here, which is how a recording failure
    stayed invisible until production held no rows against 84,748 videos."""
    client = _PublishClient(rpc_raises=Exception("db unavailable"))

    with pytest.raises(pe.NotRecorded) as ei:
        _publish(client, tmp_path)
    assert "12/wave-1/P7.mp4" in str(ei.value)


def test_a_failed_upload_never_records_a_row(tmp_path):
    """Recording a path nothing was written to is the orphan that shows a
    broken player."""
    client = _PublishClient(upload_raises=Exception("storage unavailable"))

    with pytest.raises(Exception):
        _publish(client, tmp_path)
    assert client.calls == [], "a row was recorded for a video that was never stored"


def test_an_empty_encode_is_never_published(tmp_path):
    """ffmpeg can exit 0 having written nothing; uploading it would replace a
    good video with zero bytes."""
    client = _PublishClient()

    with pytest.raises(pe.NotRecorded):
        _publish(client, tmp_path, payload=b"")
    assert client.videos.uploaded == []


def test_a_plate_with_no_wave_records_a_null_rather_than_a_number(tmp_path):
    client = _PublishClient()
    pe.publish_plate_video(
        client,
        "12/wave-none/P7.mp4",
        _encoded(tmp_path),
        experiment_id=12,
        plate_id="P7",
        wave_number=None,
        frame_count=10,
    )
    assert client.calls[0][1]["p_wave_number"] is None


def test_a_32_bit_frame_past_the_full_scale_is_refused_not_whitened():
    """Mode I is int32, so it can carry values this reduction has no scale for.

    Clipping them to 65535 does not rescale the frame — it flattens every value
    above the scale to pure white, and the result is a playable, correctly
    labelled, entirely white video. Mode F is already refused for exactly this
    reason ("carries no fixed full scale"); I has the same problem.
    """
    import numpy as np
    from PIL import Image

    # An int32 gradient over the real 32-bit range, as a machine-vision TIFF
    # from a 32-bit sensor would arrive.
    data = np.linspace(0, 2**31 - 1, 256 * 256, dtype=np.int64).reshape(256, 256)
    frame = Image.fromarray(data.astype(np.int32), mode="I")

    with pytest.raises(pe.FrameDepthUnsupported, match="past the"):
        pe._to_8bit_rgb(frame)


def test_a_32_bit_frame_inside_the_full_scale_still_works():
    """Plenty of 16-bit TIFFs load as mode I. Refusing those would reject real
    plates, so the check is on the values, not on the mode."""
    import numpy as np
    from PIL import Image

    data = np.linspace(0, 65535, 256 * 256, dtype=np.int64).reshape(256, 256)
    frame = Image.fromarray(data.astype(np.int32), mode="I")

    out = np.asarray(pe._to_8bit_rgb(frame))
    assert out.dtype == np.uint8
    # A real gradient, not a white field: the reduction preserved the range.
    assert len(np.unique(out)) > 200, "the frame flattened instead of scaling"
    assert out.max() == 255 and out.min() == 0


def test_an_unsupported_depth_is_not_reported_as_a_corrupt_file():
    """The two need different actions, so they must not read the same.

    "could not decode P7_37.tif" sends someone to rescan a plate whose file is
    perfectly fine. What actually happened is that the scanner wrote a depth
    this encoder has no scale for, which is a scanner setting, not a bad image.
    """
    import numpy as np
    from PIL import Image

    data = np.full((16, 16), 2**30, dtype=np.int32)
    buf = io.BytesIO()
    Image.fromarray(data, mode="I").save(buf, "TIFF")

    class _Images:
        def download(self, path):
            return buf.getvalue()

    with pytest.raises(pe.FrameDepthUnsupported) as ei:
        pe._fetch_frame(_Images(), "12/wave-1/P7_37.tif", LABEL)

    assert "12/wave-1/P7_37.tif" in str(ei.value), "the frame was not named"
    assert "could not decode" not in str(ei.value), (
        "an intact file was reported as a decode failure"
    )
    # Still a FrameUnreadable, so every existing handler keeps working.
    assert isinstance(ei.value, pe.FrameUnreadable)


def test_a_key_that_does_not_match_the_plate_is_refused_before_anything_is_written(
    tmp_path,
):
    """The key and the identity arrive separately; crossing them is silent.

    The upload upserts, so storing plate 8's video at plate 7's key replaces
    plate 7's video outright, and the row files it under plate 8. Nothing after
    this point can notice — plate ids repeat across waves by design, so a
    scientist looking at the wrong roots has no reason to doubt the label.
    """
    video = tmp_path / "out.mp4"
    video.write_bytes(b"\x00" * 128)
    uploaded, recorded = [], []

    class _Videos:
        def upload(self, key, data, opts):
            uploaded.append(key)

    class _Client:
        class storage:
            @staticmethod
            def from_(bucket):
                return _Videos()

        def rpc(self, name, params):
            recorded.append(params)
            raise AssertionError("a mismatched pair must never reach the database")

    with pytest.raises(pe.PlateMismatch, match="belongs at"):
        pe.publish_plate_video(
            _Client(),
            "12/wave-1/P8.mp4",  # plate 8's key ...
            str(video),
            experiment_id=12,
            plate_id="P7",  # ... recorded against plate 7
            wave_number=1,
            frame_count=86,
        )

    assert uploaded == [], "the wrong plate's video was overwritten"
    assert recorded == [], "a mismatched pair reached the database"


def test_a_key_that_matches_the_plate_is_stored_as_before(tmp_path):
    """The check must not stand between a correct caller and a stored video."""
    video = tmp_path / "out.mp4"
    video.write_bytes(b"\x00" * 128)
    uploaded = []

    class _Videos:
        def upload(self, key, data, opts):
            uploaded.append(key)

    class _Rpc:
        def execute(self):
            return None

    class _Client:
        class storage:
            @staticmethod
            def from_(bucket):
                return _Videos()

        def rpc(self, name, params):
            return _Rpc()

    row = pe.publish_plate_video(
        _Client(),
        "12/wave-1/P7.mp4",
        str(video),
        experiment_id=12,
        plate_id="P7",
        wave_number=1,
        frame_count=86,
    )

    assert uploaded == ["12/wave-1/P7.mp4"]
    assert row["object_path"] == "12/wave-1/P7.mp4"


def test_a_mismatch_is_not_reported_as_a_recording_failure(tmp_path):
    """`NotRecorded` means the video reached storage and only the row is
    missing — which the next request repairs by re-rendering. A mismatch is the
    opposite: nothing was written, on purpose, and retrying the same call
    fails the same way. Reporting one as the other sends the operator looking
    for an orphaned object that does not exist."""
    video = tmp_path / "out.mp4"
    video.write_bytes(b"\x00" * 128)

    class _Client:
        class storage:
            @staticmethod
            def from_(bucket):
                raise AssertionError("storage was touched for a mismatched pair")

    with pytest.raises(pe.PlateMismatch) as ei:
        pe.publish_plate_video(
            _Client(),
            "12/wave-1/P8.mp4",
            str(video),
            experiment_id=12,
            plate_id="P7",
            wave_number=1,
            frame_count=86,
        )

    assert not isinstance(ei.value, pe.NotRecorded), (
        "a run that stored nothing was reported as a recording failure"
    )


def test_a_frame_reaches_the_encoder_before_the_next_is_downloaded(ffmpeg, tmp_path):
    """One frame in flight at a time, which is the module's stated reason for
    existing in this shape.

    A plate image is ~97 MB decoded and about twice that while the label is
    drawn, so holding the run would be gigabytes — and the container's memory
    limit is set on the assumption that it is not held. Counting frames at the
    end cannot tell streaming from buffering; only the interleaving can, so
    this records how much has reached ffmpeg at the moment each download
    starts.
    """
    frames = _frames(5)
    client = _EncodeClient(_payloads(frames))
    written_when_downloaded = []
    fetch = client.images.download

    def recording_download(path):
        # ffmpeg is spawned by the first frame, so there is nothing to count
        # until it exists.
        written_when_downloaded.append(len(ffmpeg[0].stdin.chunks) if ffmpeg else 0)
        return fetch(path)

    client.images.download = recording_download

    assert pe.encode_plate_video(client, frames, str(tmp_path / "out.mp4")) == 5
    assert written_when_downloaded == [0, 1, 2, 3, 4], (
        "frames were collected before any was encoded — the run is being held "
        f"in memory, not streamed (saw {written_when_downloaded})"
    )


def test_earlier_frames_are_released_before_the_next_is_fetched(
    ffmpeg, tmp_path, monkeypatch
):
    """One frame in flight at a time — the module's stated reason for its shape.

    Neither the order nor the state afterwards can show this. A render that
    appends every frame to a list downloads and writes them in exactly the same
    order, and that list is a local, so it is collected the moment the function
    returns — leaving nothing to find. The regression is what is resident
    *during* the run, at ~97 MB a frame, which is what the container's memory
    limit assumes away. So this checks, at each fetch, that every frame handed
    out before it has already been collected.
    """
    import weakref

    frames = _frames(5)
    client = _EncodeClient(_payloads(frames))
    handed_out: list[weakref.ref] = []
    alive_at_each_fetch: list[int] = []
    fetch = pe._fetch_frame

    def tracking_fetch(images, path, label):
        # No gc.collect() here on purpose. Collecting first would weaken the
        # question from "was it released" to "was it releasable", and a frame
        # stranded in a reference cycle — freed only by a generational sweep
        # that triggers on allocation counts, not bytes — would pass while the
        # whole run stayed resident.
        alive_at_each_fetch.append(sum(ref() is not None for ref in handed_out))
        frame = fetch(images, path, label)
        handed_out.append(weakref.ref(frame))
        return frame

    monkeypatch.setattr(pe, "_fetch_frame", tracking_fetch)
    assert pe.encode_plate_video(client, frames, str(tmp_path / "out.mp4")) == 5

    assert alive_at_each_fetch == [0, 0, 0, 0, 0], (
        "frames from earlier in the run were still resident when the next was "
        f"fetched, so the run is accumulating rather than streaming: "
        f"{alive_at_each_fetch}"
    )


@pytest.mark.parametrize(
    "source,expected",
    [(0, 0), (255, 0), (256, 1), (4096, 16), (32768, 128), (60000, 234), (65535, 255)],
)
def test_a_16_bit_value_reduces_to_a_known_8_bit_value(source, expected):
    """The transfer function, pinned to values rather than to shape.

    Every other assertion about the reduction is a property — max above 200,
    min below 40, a spread of distinct levels. A wrapped function satisfies all
    of them: shifting by 7 instead of 8 maps the range onto 0-511, which the
    cast then truncates modulo 256, so the brightest half of the sensor folds
    back onto the darkest. The result is a plate rendered as two overlaid
    ramps, with the brightest tissue drawn black — and it passes every
    shape-based test in this file.
    """
    # A full-scale pixel in the corner, so the frame exercises the reduction
    # rather than the low-peak warning; the value under test sits at [0, 0].
    pixels = np.full((4, 4), source, dtype=np.uint16)
    pixels[-1, -1] = 65535
    frame = _deep("I;16", pixels)
    reduced = np.asarray(pe._to_8bit_rgb(frame))

    assert reduced[0, 0, 0] == expected, (
        f"{source} rendered as {reduced[0, 0, 0]}, expected {expected} "
        f"({source} >> 8)"
    )


def test_a_source_that_does_not_fill_the_scale_says_so(caplog):
    """A 12-bit sensor in a 16-bit container renders black, and must say why.

    Not a refusal. Whether a frame is accepted cannot depend on how bright it
    is, or a run fails partway through when one frame happens to be dimmer than
    another — and the fixed-scale property this reduction rests on is exactly
    that a dim frame reduces the same way a bright one does. So the scale is
    left alone and the observation is logged, which turns "the time-lapse came
    out black" from a bisect into one grep.
    """
    twelve_bit = np.full((8, 8), 4095, dtype=np.uint16)

    with caplog.at_level("WARNING"):
        reduced = np.asarray(pe._to_8bit_rgb(_deep("I;16", twelve_bit)))

    assert reduced.max() == 15, "the frame should still be reduced, just darkly"
    assert "not full-scale 16-bit" in caplog.text
    assert "4095" in caplog.text and "15/255" in caplog.text


@pytest.mark.parametrize("peak", [65535, 60000, 40000])
def test_a_source_that_fills_the_scale_is_not_warned_about(caplog, peak):
    """The warning has to stay rare, or it is noise nobody reads.

    Parametrised past exact saturation on purpose: a fixture that only peaks at
    65535 leaves the threshold free to be anything below it, so warning on
    every real frame that tops out at 65000 would pass.
    """
    ramp = np.linspace(0, peak, 64, dtype=np.uint16).reshape(8, 8)

    with caplog.at_level("WARNING"):
        pe._to_8bit_rgb(_deep("I;16", ramp))

    assert "full-scale" not in caplog.text


def test_the_encoder_opts_into_the_stall_deadline(ffmpeg, tmp_path, monkeypatch):
    """The watchdog only guards a writer that is handed a deadline.

    `test_an_ffmpeg_that_never_reads_is_killed_rather_than_blocking_forever`
    proves the writer kills a wedged child when given one, and its sibling
    proves the cyl path is left alone by not giving one. Neither proves this
    path asks for it — and dropping the argument here is invisible: every test
    still passes while a stalled encode holds its slot and its plate lock for
    the life of the worker, which is the whole reason the deadline exists.
    """
    built = {}
    real = pe.VideoWriter

    def recording_writer(*args, **kwargs):
        built.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(pe, "VideoWriter", recording_writer)
    frames = _frames(2)
    pe.encode_plate_video(_EncodeClient(_payloads(frames)), frames, str(tmp_path / "o.mp4"))

    assert built.get("deadline"), (
        "the encoder built its writer without a deadline, so a stalled ffmpeg "
        "would pin this thread and never release the encode slot or plate lock"
    )


def test_a_frame_larger_than_a_plate_is_refused_before_it_is_decoded():
    """Refused off the header, while it is still bytes.

    A file's size on disk says nothing about its size in memory. Lossless
    compression squeezes uniformity, so a blank frame of absurd dimensions is
    small on the wire and enormous once built: measured, 12000x12000 is 2 MB
    stored and 288 MB of raw pixels, and more again once it is made RGB. One
    such frame fills the container's entire budget, and the route that reaches
    this code is callable by any authenticated user.

    The check is a sanity bound, not a consistency check — such a file's header
    is perfectly honest, it is simply not a plate.
    """
    import tracemalloc

    bomb = io.BytesIO()
    Image.new("I;16", (12000, 12000)).save(bomb, "TIFF", compression="tiff_lzw")
    payload = bomb.getvalue()

    assert len(payload) < 5_000_000, "the fixture is not the small-file case"

    tracemalloc.start()
    try:
        with pytest.raises(pe.FrameUnreadable, match="megapixels"):
            pe.prepare_frame(payload, LABEL)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 50_000_000, (
        f"peaked at {peak / 1e6:.0f} MB — the frame was decoded before being "
        "refused, so the refusal saved nothing"
    )


def test_a_real_sized_plate_is_not_refused():
    """The bound has to leave real scans alone, including a larger scanner.

    A plate is 4960x6850 = 34.0 Mpx. Asserted against the constant so that
    lowering it below a real plate fails here rather than in production.
    """
    assert 4960 * 6850 < pe.MAX_PLATE_PIXELS, "a real plate no longer fits"
    assert 6000 * 8000 < pe.MAX_PLATE_PIXELS, "no room for a higher-res scanner"

    frame = pe.prepare_frame(_png(4960, 6850), LABEL)
    assert frame.shape[1] == pe.PLATE_VIDEO_WIDTH


# --------------------------------------------------------------------------
# render_plate_video — decide, encode, store, record
# --------------------------------------------------------------------------
#
# The function the route calls, and until these tests it had none: its whole
# body could be replaced with `return plan_render(...)` — never encoding,
# uploading or recording — with the entire suite still green.


def _plan(action="render", **over):
    return {
        "action": action,
        "reason": "no video stored; encoding 3 frames",
        "key": "12/wave-1/P7.mp4",
        "code": "",
        "frames": _frames(3),
        "coverage": {"state": "complete", "summary": "3 frames"},
        **over,
    }


def _wire(monkeypatch, plans, *, on_encode=None):
    """Stand in for the three calls a render makes, and record what happened.

    `plans` is what successive `plan_render` calls return, so a test can make
    the second look disagree with the first — which is the case the double plan
    exists for and the one that cannot be reached any other way.
    """
    seen = {"plans": 0, "encoded": [], "published": [], "slot_held": [], "lock_held": []}

    def plan_render(*args, **kwargs):
        seen["plans"] += 1
        return plans[min(seen["plans"] - 1, len(plans) - 1)]

    def encode(client, frames, out_path):
        # Sampled while the render is in flight: afterwards both are released
        # and the difference between holding them and not is invisible.
        seen["slot_held"].append(pe._encode_slots._value)
        seen["lock_held"].append(pe._plate_locks["12/wave-1/P7.mp4"].locked())
        seen["encoded"].append(out_path)
        if on_encode is not None:
            on_encode()
        return len(frames)

    def publish(client, key, video_path, **kwargs):
        seen["published"].append((key, kwargs["frame_count"]))
        return {"object_path": key, "frame_count": kwargs["frame_count"]}

    monkeypatch.setattr(pe, "plan_render", plan_render)
    monkeypatch.setattr(pe, "encode_plate_video", encode)
    monkeypatch.setattr(pe, "publish_plate_video", publish)
    return seen


def test_a_render_encodes_stores_and_records(monkeypatch):
    """The body is the feature. Replacing it with the plan alone left the whole
    suite green, so this asserts each of the three things actually happened."""
    seen = _wire(monkeypatch, [_plan()])

    result = pe.render_plate_video(object(), 12, "P7", 1)

    assert result["action"] == "rendered"
    assert len(seen["encoded"]) == 1, "nothing was encoded"
    assert seen["published"] == [("12/wave-1/P7.mp4", 3)], "nothing was recorded"
    assert result["recorded"]["frame_count"] == 3


def test_the_second_look_under_the_lock_turns_a_race_into_a_keep(monkeypatch):
    """Between deciding and holding the lock, another request may have rendered
    this plate. Re-encoding would overwrite a video identical to the one about
    to be made, so the second plan is what makes the first one safe."""
    seen = _wire(monkeypatch, [_plan(), _plan(action="keep", reason="already covers 3")])

    result = pe.render_plate_video(object(), 12, "P7", 1)

    assert result["action"] == "keep"
    assert seen["plans"] == 2, "the plan was not taken again under the lock"
    assert seen["encoded"] == [], "it encoded over a video that was already current"


def test_a_plan_that_says_no_never_reaches_the_lock(monkeypatch):
    seen = _wire(monkeypatch, [_plan(action="refuse", reason="no captures")])

    assert pe.render_plate_video(object(), 12, "P7", 1)["action"] == "refuse"
    assert seen["plans"] == 1
    assert seen["encoded"] == []


def test_both_guards_are_held_while_the_encode_runs(monkeypatch):
    """Sampled inside the encode: a slot consumed and the plate's lock held.
    Removing either leaves every other assertion in this file unchanged."""
    seen = _wire(monkeypatch, [_plan()])

    pe.render_plate_video(object(), 12, "P7", 1)

    assert seen["slot_held"] == [pe.MAX_CONCURRENT_ENCODES - 1], "no encode slot was taken"
    assert seen["lock_held"] == [True], "the plate was not locked while it rendered"


def test_the_plate_lock_is_taken_before_the_slot(monkeypatch):
    """The order decides which of two true things the caller is told.

    With every slot taken and this plate one of the four rendering, slot first
    answers "the encoder is busy" and lock first answers "this plate is already
    being rendered" — the second is the one the caller can act on. Nothing is
    held while waiting either way, because neither acquire waits.
    """
    order = []
    real_slot, real_lock = pe.encode_slot, pe.plate_lock

    @contextmanager
    def slot(*a, **k):
        order.append("slot")
        with real_slot(*a, **k):
            yield

    @contextmanager
    def lock(*a, **k):
        order.append("lock")
        with real_lock(*a, **k):
            yield

    monkeypatch.setattr(pe, "encode_slot", slot)
    monkeypatch.setattr(pe, "plate_lock", lock)
    _wire(monkeypatch, [_plan()])

    pe.render_plate_video(object(), 12, "P7", 1)

    assert order == ["lock", "slot"]


def test_a_full_encoder_still_says_which_plate_is_rendering(monkeypatch):
    """The reason the order is what it is, from the caller's side."""
    _wire(monkeypatch, [_plan()])
    key = "12/wave-1/P7.mp4"
    held = [pe.encode_slot() for _ in range(pe.MAX_CONCURRENT_ENCODES)]
    for slot in held:
        slot.__enter__()
    try:
        with pe.plate_lock(key):
            with pytest.raises(pe.PlateBusy) as caught:
                pe.render_plate_video(object(), 12, "P7", 1)
    finally:
        for slot in held:
            slot.__exit__(None, None, None)

    assert key in str(caught.value)
    assert pe._encode_slots._value == pe.MAX_CONCURRENT_ENCODES


def test_the_output_name_never_comes_from_the_plate_id(monkeypatch):
    """The plate id is caller-supplied. A constant name keeps it out of the
    filesystem and off the ffmpeg command line, where a leading dash is an
    option and a scheme is a destination."""
    seen = _wire(monkeypatch, [_plan()])

    pe.render_plate_video(object(), 12, "P7", 1)

    written = seen["encoded"][0]
    assert written.endswith("plate.mp4")
    assert "P7" not in os.path.basename(written)


@pytest.mark.parametrize(
    "plans,on_encode",
    [
        ([_plan()], None),
        ([_plan(), _plan(action="keep")], None),
        ([_plan()], lambda: (_ for _ in ()).throw(pe.FrameUnreadable("bad frame"))),
    ],
    ids=["success", "keep-under-the-lock", "the-encode-raises"],
)
def test_the_slot_and_the_lock_are_handed_back_on_every_path(monkeypatch, plans, on_encode):
    """A slot leaked on any path takes a permanent bite out of capacity, and a
    plate whose lock is never released can never be rendered again."""
    _wire(monkeypatch, plans, on_encode=on_encode)
    before = pe._encode_slots._value

    try:
        pe.render_plate_video(object(), 12, "P7", 1)
    except pe.FrameUnreadable:
        pass

    assert pe._encode_slots._value == before, "an encode slot was not released"
    assert not pe._plate_locks["12/wave-1/P7.mp4"].locked(), "the plate stayed locked"


def test_a_frame_far_larger_than_a_scan_is_refused_before_it_is_decoded():
    """A bound on what this path will hold, not a check on the recorded size.

    The whole-plate guard sums `gravi_images.file_size_bytes`, which is what the
    desktop wrote at upload. That is a record, not something to bet memory on:
    an interrupted upload, a resumed transfer or an app bug all leave it
    disagreeing with the object, and without this the download holds whatever
    actually arrives.
    """
    oversized = b"\x00" * (pe.MAX_FRAME_BYTES + 1)
    images = _Images({"12/wave-1/P7_0.tif": oversized})

    with pytest.raises(pe.FrameUnreadable, match="MB a plate frame can be") as ei:
        pe._fetch_frame(images, "12/wave-1/P7_0.tif", LABEL)

    assert "12/wave-1/P7_0.tif" in str(ei.value), "the frame was not named"


def test_a_normal_sized_frame_is_not_refused():
    """A real frame is ~59 MB nominal and 93 MB for a detailed 16-bit scan, so
    the cap has to sit clear of both or it refuses the plates it exists for."""
    assert pe.MAX_FRAME_BYTES > 100 * 1024**2, "a real 16-bit frame would be refused"

    frame = pe._fetch_frame(
        _Images({"12/wave-1/P7_0.tif": _png(400, 600)}), "12/wave-1/P7_0.tif", LABEL
    )
    assert frame.dtype == np.uint8
