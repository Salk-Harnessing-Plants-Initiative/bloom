"""
VideoWriter - FFmpeg-based video encoding utility.

Streams images to an FFmpeg subprocess for efficient H.264 MP4 encoding.
Ported from services/video-worker so the workflows API can build videos inline.

close() surfaces a failed encode as VideoEncodeError so a truncated MP4 is never
mistaken for a good one — callers must not upload the output unless close()
returns cleanly.
"""

import subprocess
import threading
import time

import numpy as np

# Hard ceiling on a single encode; a stuck ffmpeg is killed rather than pinning
# the synchronous request's worker thread indefinitely.
ENCODE_TIMEOUT_SECONDS = 120.0

# How much of ffmpeg's stderr is kept for the error message. Bounded because
# the drain runs for the whole encode: a stream the child can flood must not be
# one this process holds entire.
STDERR_TAIL_BYTES = 8192

# How long close() waits for the drain to reach EOF once ffmpeg has exited.
STDERR_DRAIN_TIMEOUT_SECONDS = 5.0


class _StderrTail:
    """Reads ffmpeg's stderr for the life of the encode, keeping the tail.

    Drained continuously rather than read in close(): the pipe buffer is ~64KB,
    and a child that fills it stops reading stdin, so add() blocks in write()
    and close() — the only place a timeout is applied — is never reached. The
    encode then hangs for the life of the process, holding whatever the caller
    holds around it.
    """

    def __init__(self, stream):
        self._stream = stream
        self._tail = bytearray()
        self._guard = threading.Lock()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        try:
            while True:
                chunk = self._stream.read(4096)
                if not chunk:
                    break
                with self._guard:
                    self._tail.extend(chunk)
                    del self._tail[:-STDERR_TAIL_BYTES]
        except Exception as exc:
            # A drain that raises must not take the encode with it. Record why
            # rather than swallowing it: a silently dead drain reports ffmpeg's
            # failure with an empty reason, and looks identical to a clean exit.
            with self._guard:
                self._tail.extend(f"[stderr drain failed: {exc!r}]".encode())

    def finish(self, timeout: float = STDERR_DRAIN_TIMEOUT_SECONDS) -> bytes:
        """The tail, once the pipe has reached EOF. Call after ffmpeg exits."""
        self._thread.join(timeout)
        try:
            self._stream.close()
        except Exception:
            pass
        with self._guard:
            return bytes(self._tail)


class VideoEncodeError(RuntimeError):
    """ffmpeg exited non-zero, timed out, or produced no output."""


class VideoWriter:
    """Write video frames to a file using FFmpeg."""

    def __init__(
        self,
        filename: str,
        fps: float = 30.0,
        deadline: float | None = None,
    ):
        """Initialize the writer for `filename` at `fps` frames per second.

        `deadline` is how long ffmpeg may go without accepting a frame before
        it is killed — a stall, not a duration. A write to a full pipe blocks
        with no timeout of its own, and the wait in close() cannot help because
        a stalled encode never reaches close(); the caller's `finally`, and so
        its concurrency slot, is held for the life of the worker.

        Opt-in. A long run is not a stuck one, and the caller that wants this
        is the one that knows what a stall looks like for its own workload.
        """
        self.filename = filename
        self.fps = fps
        self.deadline = deadline
        self.process = None
        self.width = None
        self.height = None
        self._stderr = None
        self._watchdog = None
        self._deadline_expired = False
        self._deadline_guard = threading.Lock()
        self._last_progress = None

    @staticmethod
    def _to_even(img: np.ndarray) -> np.ndarray:
        """Pad bottom/right by 1px so width and height are even.

        yuv420p (required for broad browser playback) needs even dimensions;
        decimated frames are often odd, which makes libx264 exit non-zero.
        Padding never drops image content.
        """
        h, w = img.shape[:2]
        if h % 2 or w % 2:
            img = np.pad(img, ((0, h % 2), (0, w % 2), (0, 0)), mode="edge")
        return img

    def add(self, img: np.ndarray):
        """Add one frame (H×W×C or H×W grayscale numpy array)."""
        if img.ndim == 2:
            # Grayscale - convert to RGB
            img = np.stack([img, img, img], axis=-1)

        img = self._to_even(img)

        if self.process is None:
            self._open(img.shape[1], img.shape[0])
        elif (img.shape[1], img.shape[0]) != (self.width, self.height):
            # ffmpeg reads raw frames by byte count, so a frame of a different
            # size shears every frame after it and still exits 0.
            raise ValueError(
                f"frame is {img.shape[1]}x{img.shape[0]}, but the video was "
                f"opened at {self.width}x{self.height}"
            )

        # Ensure uint8
        if img.dtype != np.uint8:
            img = img.astype(np.uint8)

        try:
            self.process.stdin.write(img.tobytes())
        except (BrokenPipeError, OSError):
            if self._deadline_expired:
                raise VideoEncodeError(
                    f"ffmpeg accepted no frame for {self.deadline}s and was killed"
                ) from None
            # Any other broken pipe means ffmpeg exited on its own; close()
            # reads its status and reports the reason it gave.
            raise

        # ffmpeg took a frame, so it is working rather than stuck. Recorded
        # under the guard the watchdog reads it under.
        if self.deadline is not None:
            with self._deadline_guard:
                self._last_progress = time.monotonic()

    def _open(self, width: int, height: int):
        """Open the FFmpeg process, inferring video dimensions from the first frame."""
        self.width = width
        self.height = height

        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output file
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{width}x{height}",
            "-pix_fmt",
            "rgb24",
            "-r",
            str(self.fps),
            "-i",
            "-",  # Read from stdin
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-loglevel",
            "error",  # few enough lines that the kept tail is the whole story
            "-nostats",
            self.filename,
        ]

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._stderr = _StderrTail(self.process.stderr)

        # Killing the child is what unblocks a write waiting on a full pipe:
        # the pipe closes, the write raises, and the caller's finally can run.
        # A timeout inside close() cannot do this — a stalled encode never
        # reaches close().
        self._deadline_expired = False
        if self.deadline is not None:
            self._last_progress = time.monotonic()
            self._arm(self.deadline)

    def _arm(self, delay: float) -> None:
        """Schedule the stall check. Caller holds `_deadline_guard`, or is _open."""
        self._watchdog = threading.Timer(delay, self._kill_if_stalled)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _kill_if_stalled(self):
        """Kill ffmpeg if it has accepted nothing for a whole deadline.

        Re-arms instead of killing when a frame landed since the timer was set.
        The alternative — one deadline for the whole encode — would time the
        caller's download loop as well, and a plate that is merely large would
        be killed and reported as a stuck ffmpeg.

        close() clears `self.process` before it waits, so a timer that fires
        alongside close() finds None here and returns without touching a
        process that was finishing normally.
        """
        with self._deadline_guard:
            proc = self.process
            if proc is None or self.deadline is None:
                return
            idle = time.monotonic() - self._last_progress
            if idle < self.deadline:
                self._arm(self.deadline - idle)
                return
            self._deadline_expired = True
            try:
                proc.kill()
            except OSError:
                # Already gone; close() reads the exit status either way.
                pass

    def close(self, timeout: float = ENCODE_TIMEOUT_SECONDS):
        """Finalize the video, raising VideoEncodeError if ffmpeg failed.

        Checks the exit code (a non-zero encode must not pass as success),
        bounds the wait so a stuck ffmpeg can't pin the worker, and guards the
        stdin flush so a dead-pipe broken flush surfaces as a clean error.
        """
        if self.process is None:
            return

        proc, self.process = self.process, None
        tail, self._stderr = self._stderr, None

        # self.process is already None above, which is what makes a timer
        # firing alongside this a no-op; cancelling only saves the wakeup.
        with self._deadline_guard:
            if self._watchdog is not None:
                self._watchdog.cancel()
                self._watchdog = None

        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            # ffmpeg already exited; returncode below carries the real reason.
            pass

        try:
            proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            timed_out = True

        # Collected after the wait either way, so the tail is complete and the
        # stderr pipe is closed even on the path that kills ffmpeg.
        err = tail.finish() if tail is not None else b""
        msg = err.decode("utf-8", "replace").strip()[-2000:]

        if timed_out:
            raise VideoEncodeError(f"ffmpeg timed out after {timeout}s; killed: {msg}")

        if proc.returncode != 0:
            if self._deadline_expired:
                # Killed between the last frame and here. Without this the
                # report is "ffmpeg exited -9" with an empty tail, which reads
                # as a crash rather than as the deadline doing its job.
                raise VideoEncodeError(
                    f"ffmpeg accepted no frame for {self.deadline}s and was killed"
                )
            raise VideoEncodeError(f"ffmpeg exited {proc.returncode}: {msg}")
