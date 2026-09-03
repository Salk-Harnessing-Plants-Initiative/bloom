"""Unit tests for VideoWriter lifecycle safety (no real ffmpeg needed).

Covers even-dimension padding, the frame-size guard in add(), and the close()
contract: a non-zero exit, a timeout, and a broken-pipe stdin flush must all be
handled so a failed encode can never masquerade as success.
"""

import subprocess

import numpy as np
import pytest

from video_writer import VideoWriter, VideoEncodeError


# --- even-dimension padding -------------------------------------------------


def test_to_even_pads_odd_dimensions():
    img = np.zeros((5, 7, 3), dtype=np.uint8)  # odd H and W
    out = VideoWriter._to_even(img)
    assert out.shape[:2] == (6, 8)


def test_to_even_pads_only_the_odd_axis():
    """Both dimensions odd would let height and width be swapped."""
    assert VideoWriter._to_even(np.zeros((200, 301, 3), np.uint8)).shape == (
        200,
        302,
        3,
    )
    assert VideoWriter._to_even(np.zeros((201, 300, 3), np.uint8)).shape == (
        202,
        300,
        3,
    )


def test_to_even_pads_the_bottom_and_right_not_the_top_and_left():
    """Padding at the top shifts every row down; edge-replication makes the
    corner pixel look right either way, so compare the whole original block."""
    img = np.arange(3 * 3 * 3, dtype=np.uint8).reshape(3, 3, 3)
    assert np.array_equal(VideoWriter._to_even(img)[:3, :3], img)


def test_to_even_leaves_even_unchanged():
    img = np.zeros((4, 8, 3), dtype=np.uint8)
    out = VideoWriter._to_even(img)
    assert out.shape[:2] == (4, 8)
    assert out is img  # no copy when already even


# --- close() lifecycle ------------------------------------------------------


class _FakeStream:
    def __init__(self, data=b"", raise_on_close=None):
        self._data = data
        self.closed = False
        self._raise_on_close = raise_on_close

    def read(self):
        return self._data

    def write(self, _b):
        pass

    def close(self):
        self.closed = True
        if self._raise_on_close is not None:
            raise self._raise_on_close


class _FakeProc:
    """Stands in for a Popen — drives close() down each branch."""

    def __init__(self, returncode=0, stderr=b"", wait_exc=None, stdin_close_exc=None):
        self.returncode = returncode
        self.stderr = _FakeStream(stderr)
        self.stdin = _FakeStream(raise_on_close=stdin_close_exc)
        self._wait_exc = wait_exc
        self.killed = False

    def wait(self, timeout=None):
        if self._wait_exc is not None:
            exc, self._wait_exc = self._wait_exc, None  # raise once; succeed after kill
            raise exc
        return self.returncode

    def kill(self):
        self.killed = True


def _writer_with(proc):
    w = VideoWriter(filename="/tmp/unused.mp4")
    w.process = proc
    return w


def test_close_clean_on_zero_exit():
    w = _writer_with(_FakeProc(returncode=0))
    w.close()  # must not raise
    assert w.process is None


def test_close_raises_and_reports_stderr_on_nonzero_exit():
    proc = _FakeProc(returncode=1, stderr=b"height not divisible by 2")
    w = _writer_with(proc)
    with pytest.raises(VideoEncodeError) as ei:
        w.close()
    assert "height not divisible by 2" in str(ei.value)
    assert proc.stderr.closed


def test_close_kills_and_raises_on_timeout():
    proc = _FakeProc(wait_exc=subprocess.TimeoutExpired("ffmpeg", 1))
    w = _writer_with(proc)
    with pytest.raises(VideoEncodeError):
        w.close(timeout=0.01)
    assert proc.killed


def test_close_guards_broken_pipe_on_stdin_close():
    # A dead ffmpeg makes the stdin flush raise; close() must swallow that and
    # let the return code carry the outcome (here: success).
    proc = _FakeProc(returncode=0, stdin_close_exc=BrokenPipeError())
    w = _writer_with(proc)
    w.close()  # must not raise
    assert w.process is None


def test_close_on_unopened_writer_is_noop():
    VideoWriter(filename="/tmp/unused.mp4").close()  # process is None


class _RecordingStream(_FakeStream):
    """Keeps what was written, so a frame's byte count can be checked."""

    def __init__(self):
        super().__init__()
        self.chunks = []

    def write(self, b):
        self.chunks.append(b)


def _opened_writer(width, height):
    """A writer already streaming, so `add` takes the size-check branch."""
    w = _writer_with(_FakeProc(returncode=0))
    w.width, w.height = width, height
    w.process.stdin = _RecordingStream()
    return w


@pytest.mark.parametrize(
    "shape",
    [
        (150, 250, 3),  # both axes differ
        (244, 300, 3),  # same width, taller — what annotate produces
        (200, 400, 3),  # same height, wider
    ],
)
def test_add_refuses_a_frame_that_changes_the_size(shape):
    """ffmpeg reads raw frames by byte count, so a differently sized frame
    shears every frame after it and still exits 0. One axis is enough."""
    w = _opened_writer(300, 200)
    with pytest.raises(ValueError, match="opened at 300x200"):
        w.add(np.zeros(shape, dtype=np.uint8))
    assert w.process.stdin.chunks == [], "the bad frame must not reach ffmpeg"


def test_add_writes_the_frame_once_and_in_row_order():
    """Byte count alone would accept a transposed frame, or the same frame
    written twice."""
    frame = np.arange(200 * 300 * 3, dtype=np.uint32).astype(np.uint8)
    frame = frame.reshape(200, 300, 3)
    w = _opened_writer(300, 200)
    w.add(frame)
    assert len(w.process.stdin.chunks) == 1
    assert w.process.stdin.chunks[0] == frame.tobytes()


def test_add_compares_the_size_after_padding_to_even():
    """_to_even runs first, so an odd frame that pads up to the video's size
    is the same frame, not a size change."""
    w = _opened_writer(300, 200)
    w.add(np.zeros((199, 299, 3), dtype=np.uint8))
    assert len(w.process.stdin.chunks[0]) == 300 * 200 * 3


def test_add_refuses_a_grayscale_frame_of_the_wrong_size():
    """Grayscale is widened to RGB first, so the check still sees pixels."""
    w = _opened_writer(300, 200)
    with pytest.raises(ValueError, match="opened at 300x200"):
        w.add(np.zeros((150, 250), dtype=np.uint8))


def test_the_first_frame_opens_the_video_and_the_second_must_match(monkeypatch):
    """Drives the guard through a real _open. The other tests set width and
    height by hand, so they would still pass if _open stopped setting them."""
    opened = {}

    class _Popen:
        def __init__(self, cmd, **kw):
            opened["cmd"] = cmd
            self.stdin = _RecordingStream()
            self.stderr = _FakeStream()
            self.returncode = 0

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    w = VideoWriter(filename="/tmp/unused.mp4")

    w.add(np.zeros((200, 300, 3), dtype=np.uint8))
    assert (w.width, w.height) == (300, 200)
    assert "300x200" in opened["cmd"]

    with pytest.raises(ValueError, match="opened at 300x200"):
        w.add(np.zeros((150, 250, 3), dtype=np.uint8))
    assert len(w.process.stdin.chunks) == 1


@pytest.mark.parametrize("shape", [(200, 300, 4), (200, 300, 2), (200, 300, 1), (5,)])
def test_add_refuses_a_frame_that_is_not_three_channel(shape):
    """The video is written as rgb24. An image with alpha decodes to 4
    channels, changing the bytes per frame without changing width or height."""
    w = _opened_writer(300, 200)
    with pytest.raises(ValueError, match="3-channel"):
        w.add(np.zeros(shape, dtype=np.uint8))
    assert w.process.stdin.chunks == []


def test_add_still_widens_grayscale_before_the_channel_check():
    """Mono capture stays supported: 2D is expanded to RGB first."""
    w = _opened_writer(300, 200)
    w.add(np.zeros((200, 300), dtype=np.uint8))
    assert len(w.process.stdin.chunks[0]) == 300 * 200 * 3
