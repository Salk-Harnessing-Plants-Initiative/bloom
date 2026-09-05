"""Unit tests for VideoWriter lifecycle safety (no real ffmpeg needed).

Covers even-dimension padding, the frame-size guard in add(), and the close()
contract: a non-zero exit, a timeout, and a broken-pipe stdin flush must all be
handled so a failed encode can never masquerade as success.
"""

import subprocess
import threading
import time

import numpy as np
import pytest

from video_writer import VideoWriter, VideoEncodeError, _StderrTail


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

    def read(self, size=-1):
        """Hands the payload over once, then reports EOF like a closed pipe."""
        data, self._data = self._data, b""
        return data

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
    # What _open() attaches: stderr is drained for the life of the encode, so
    # close() reads a tail rather than a pipe that may never have been emptied.
    w._stderr = _StderrTail(proc.stderr)
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


# --- stderr is drained, not left to fill ------------------------------------
#
# Real child processes here. The pipe-buffer deadlock only exists between two
# processes, and a fake stdin never blocks, so a mocked ffmpeg cannot show it.
#
# Every encode below runs off the test's own thread and every wait is bounded.
# A regression here blocks rather than raises, and a blocked assertion is a
# stuck CI job with no failure in it.

import sys  # noqa: E402

import video_writer  # noqa: E402

# Comfortably past the ~64KB pipe buffer a child blocks on.
_FLOOD_BYTES = 300_000

# Long enough that a machine under load is not mistaken for a deadlock.
_NOT_HANGING_SECONDS = 30.0

# Frames big enough that the stdin buffer fills too, so a child that stops
# reading stdin blocks the writer rather than being quietly absorbed.
_BIG_FRAME = np.zeros((200, 300, 3), dtype=np.uint8)  # 180KB each

_FLOOD_THEN_DRAIN_STDIN = (
    "import sys\n"
    f"sys.stderr.buffer.write(b'x' * {_FLOOD_BYTES})\n"
    "sys.stderr.buffer.flush()\n"
    "sys.stdin.buffer.read()\n"
)


def _stub_ffmpeg(monkeypatch, script: str):
    """Run `script` wherever the code would have run ffmpeg."""
    real_popen = subprocess.Popen

    def _spawn(cmd, **kwargs):
        return real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(video_writer.subprocess, "Popen", _spawn)


def _encode_off_thread(writer, frames, close_timeout=10.0):
    """Run a whole encode elsewhere, and return how it went.

    Returns the exception the encode raised (or None) and the drain the first
    frame's `_open` attached — close() clears it, so it is taken while the
    encode is still running. Every touch of the writer happens on this thread,
    because `add` is the call that blocks when the drain is missing.
    """
    finished = threading.Event()
    outcome = []
    drains = []

    def run():
        try:
            for frame in frames:
                writer.add(frame)
                if not drains:
                    drains.append(writer._stderr)
            writer.close(timeout=close_timeout)
            outcome.append(None)
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            outcome.append(exc)
        finally:
            finished.set()

    threading.Thread(target=run, daemon=True).start()

    assert finished.wait(_NOT_HANGING_SECONDS), (
        "the encode never finished: ffmpeg's stderr was left to fill, so the "
        "child stopped reading stdin and add() blocked in write() — and "
        "close(), which holds the only timeout on this path, is never reached"
    )
    return outcome[0], (drains[0] if drains else None)


def test_a_flooding_ffmpeg_does_not_block_the_encode(monkeypatch, tmp_path):
    """A child that fills its stderr pipe stops reading stdin, and add() then
    blocks in write() with no timeout on it. The encode hangs for the life of
    the process, and whatever the caller wrapped around it — an encode slot, a
    plate's lock — is held just as long, while /health still passes.
    -loglevel error makes that unlikely, not impossible.
    """
    _stub_ffmpeg(monkeypatch, _FLOOD_THEN_DRAIN_STDIN)

    writer = VideoWriter(filename=str(tmp_path / "out.mp4"))
    failure, _ = _encode_off_thread(writer, [_BIG_FRAME] * 4)  # 720KB of stdin

    assert failure is None, f"the encode failed: {failure!r}"


def test_a_flooded_stderr_is_kept_bounded(monkeypatch, tmp_path):
    """The drain runs for the whole encode, so it must not accumulate whatever
    a child chooses to emit."""
    _stub_ffmpeg(monkeypatch, _FLOOD_THEN_DRAIN_STDIN)

    writer = VideoWriter(filename=str(tmp_path / "out.mp4"))
    failure, drain = _encode_off_thread(writer, [_BIG_FRAME])

    assert failure is None, f"the encode failed: {failure!r}"
    assert drain is not None, "stderr was never drained"
    assert len(drain._tail) <= video_writer.STDERR_TAIL_BYTES, (
        f"kept {len(drain._tail)} bytes of a {_FLOOD_BYTES}-byte flood"
    )


def test_the_end_of_a_flood_survives_to_the_error_message(monkeypatch, tmp_path):
    """The tail is kept rather than the head: ffmpeg says why it is giving up
    last, after whatever it complained about on the way."""
    _stub_ffmpeg(
        monkeypatch,
        "import sys\n"
        f"sys.stderr.buffer.write(b'noise' * {_FLOOD_BYTES // 5})\n"
        "sys.stderr.buffer.write(b'height not divisible by 2\\n')\n"
        "sys.stderr.buffer.flush()\n"
        "sys.stdin.buffer.read()\n"
        "sys.exit(1)\n",
    )

    writer = VideoWriter(filename=str(tmp_path / "out.mp4"))
    failure, _ = _encode_off_thread(writer, [_BIG_FRAME])

    assert isinstance(failure, VideoEncodeError), f"got {failure!r}"
    assert "height not divisible by 2" in str(failure)


def test_a_stuck_ffmpeg_is_still_killed_within_the_timeout(monkeypatch, tmp_path):
    """The drain must not become a second place close() can hang: it is joined
    only after ffmpeg has been waited for or killed."""
    _stub_ffmpeg(
        monkeypatch,
        "import sys, time\nsys.stdin.buffer.read()\ntime.sleep(120)\n",
    )

    writer = VideoWriter(filename=str(tmp_path / "out.mp4"))
    started = time.monotonic()
    failure, _ = _encode_off_thread(writer, [_BIG_FRAME], close_timeout=1)

    assert isinstance(failure, VideoEncodeError) and "timed out" in str(failure)
    assert time.monotonic() - started < 15, "close() outran its own timeout"


def test_a_drain_that_cannot_read_says_so_instead_of_reporting_nothing():
    """A stderr stream the drain cannot read must not look like a clean exit.

    The drain reads in fixed-size chunks. A stream whose `read` takes no size
    argument raises on the very first call, and swallowing that silently left
    the thread dead and the tail empty — so ffmpeg's failure was reported with
    no reason after it, indistinguishable from ffmpeg exiting quietly. This is
    not hypothetical: the cylinder pipeline's own test double had exactly that
    signature, and three tests exercised a drain that died immediately while
    still passing.
    """

    class _Unreadable:
        def read(self):  # no size parameter — the shape of the real bug
            return b""

        def close(self):
            pass

    drain = _StderrTail(_Unreadable())
    tail = drain.finish(timeout=1.0).decode()
    assert "stderr drain failed" in tail, (
        "a drain that died left an empty tail, so a failed encode reports no reason"
    )
    assert "TypeError" in tail, "the tail should name what actually went wrong"


def test_an_ffmpeg_that_never_reads_is_killed_rather_than_blocking_forever(
    monkeypatch, tmp_path
):
    """A write to a full pipe has no timeout of its own, so the deadline has to
    kill the child; nothing else can unblock it.

    The sibling test above feeds a child that drains stdin before sleeping, so
    `add` never blocks and only close()'s wait is exercised. Here the child
    never reads at all. Before the writer had its own deadline this pinned the
    calling thread for the life of the worker — and because the caller releases
    its encode slot and plate lock in a `finally`, a body that never returns
    meant the slot and the lock were never handed back: capacity lost, and that
    plate unrenderable, until the container restarted.
    """
    _stub_ffmpeg(monkeypatch, "import time\ntime.sleep(120)\n")

    writer = VideoWriter(filename=str(tmp_path / "out.mp4"), deadline=1.0)
    started = time.monotonic()
    # Enough frames to overrun the 64KB pipe buffer and block on the write.
    failure, _ = _encode_off_thread(writer, [_BIG_FRAME] * 4, close_timeout=5)
    elapsed = time.monotonic() - started

    assert failure is not None, "a wedged ffmpeg completed successfully"
    assert isinstance(failure, VideoEncodeError), (
        f"the encode must fail as an encode error, got {failure!r}"
    )
    assert "stopped accepting frames" in str(failure)
    assert elapsed < 20, f"the deadline did not fire: took {elapsed:.1f}s"


def test_a_healthy_encode_is_not_killed_by_its_own_deadline(monkeypatch, tmp_path):
    """The watchdog must be cancelled once close() takes over, or a slow but
    perfectly good encode is killed by a timer guarding nothing."""
    _stub_ffmpeg(monkeypatch, "import sys\nsys.stdin.buffer.read()\n")

    writer = VideoWriter(filename=str(tmp_path / "out.mp4"), deadline=0.5)
    writer.add(_BIG_FRAME)
    writer.close(timeout=5)
    time.sleep(1.0)  # past the deadline, with the encode already finished

    assert writer._watchdog is None, "the watchdog outlived the encode"


def test_the_deadline_cannot_kill_an_encode_that_already_finished(monkeypatch, tmp_path):
    """Disarming has to survive a timer that is already running.

    `cancel()` only stops a timer that has not fired yet. One that fired a
    moment before close() is past cancelling, so without the disarm flag it
    would kill a process that finished normally — a healthy encode failing
    rarely, with nothing in the logs to explain it.
    """
    _stub_ffmpeg(monkeypatch, "import sys\nsys.stdin.buffer.read()\n")

    writer = VideoWriter(filename=str(tmp_path / "out.mp4"), deadline=60.0)
    writer.add(_BIG_FRAME)
    writer.close(timeout=5)

    # Exactly what a timer firing at the wrong moment would do.
    writer._kill_past_deadline()
    assert not writer._deadline_expired, "a disarmed deadline still fired"
