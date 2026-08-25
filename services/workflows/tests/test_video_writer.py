"""Unit tests for VideoWriter lifecycle safety (no real ffmpeg needed).

Covers even-dimension padding and the close() contract: a non-zero exit, a
timeout, and a broken-pipe stdin flush must all be handled so a failed encode
can never masquerade as success.
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


def test_add_refuses_a_frame_that_changes_the_size():
    """ffmpeg reads raw frames by byte count, so a differently sized frame
    shears every frame after it and still exits 0."""
    w = _opened_writer(300, 200)
    with pytest.raises(ValueError, match="opened at 300x200"):
        w.add(np.zeros((150, 250, 3), dtype=np.uint8))
    assert w.process.stdin.chunks == [], "the bad frame must not reach ffmpeg"


def test_add_accepts_a_frame_that_matches():
    w = _opened_writer(300, 200)
    w.add(np.zeros((200, 300, 3), dtype=np.uint8))
    assert len(w.process.stdin.chunks[0]) == 300 * 200 * 3


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
