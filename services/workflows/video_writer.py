"""
VideoWriter - FFmpeg-based video encoding utility.

Streams images to an FFmpeg subprocess for efficient H.264 MP4 encoding.
Ported from services/video-worker so the workflows API can build videos inline.

close() surfaces a failed encode as VideoEncodeError so a truncated MP4 is never
mistaken for a good one — callers must not upload the output unless close()
returns cleanly.
"""

import subprocess
import numpy as np

# Hard ceiling on a single encode; a stuck ffmpeg is killed rather than pinning
# the synchronous request's worker thread indefinitely.
ENCODE_TIMEOUT_SECONDS = 120.0


class VideoEncodeError(RuntimeError):
    """ffmpeg exited non-zero, timed out, or produced no output."""


class VideoWriter:
    """Write video frames to a file using FFmpeg."""

    def __init__(self, filename: str, fps: float = 30.0):
        """Initialize the writer for `filename` at `fps` frames per second."""
        self.filename = filename
        self.fps = fps
        self.process = None
        self.width = None
        self.height = None

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

        # Ensure uint8
        if img.dtype != np.uint8:
            img = img.astype(np.uint8)

        self.process.stdin.write(img.tobytes())

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
            "error",  # keep stderr small enough to read into memory
            "-nostats",
            self.filename,
        ]

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def close(self, timeout: float = ENCODE_TIMEOUT_SECONDS):
        """Finalize the video, raising VideoEncodeError if ffmpeg failed.

        Checks the exit code (a non-zero encode must not pass as success),
        bounds the wait so a stuck ffmpeg can't pin the worker, and guards the
        stdin flush so a dead-pipe broken flush surfaces as a clean error.
        """
        if self.process is None:
            return

        proc, self.process = self.process, None

        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            # ffmpeg already exited; returncode below carries the real reason.
            pass

        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise VideoEncodeError(f"ffmpeg timed out after {timeout}s; killed")

        err = b""
        if proc.stderr:
            try:
                err = proc.stderr.read()
            finally:
                proc.stderr.close()

        if proc.returncode != 0:
            msg = err.decode("utf-8", "replace").strip()[-2000:]
            raise VideoEncodeError(f"ffmpeg exited {proc.returncode}: {msg}")
