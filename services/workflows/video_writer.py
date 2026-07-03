"""
VideoWriter - FFmpeg-based video encoding utility.

Streams images to an FFmpeg subprocess for efficient H.264 MP4 encoding.
Ported from services/video-worker so the workflows API can build videos inline.
"""

import subprocess
import numpy as np


class VideoWriter:
    """Write video frames to a file using FFmpeg."""

    def __init__(self, filename: str, fps: float = 30.0):
        """Initialize the writer for `filename` at `fps` frames per second."""
        self.filename = filename
        self.fps = fps
        self.process = None
        self.width = None
        self.height = None

    def add(self, img: np.ndarray):
        """Add one frame (H×W×C or H×W grayscale numpy array)."""
        if img.ndim == 2:
            # Grayscale - convert to RGB
            img = np.stack([img, img, img], axis=-1)

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
            self.filename,
        ]

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def close(self):
        """Finalize the video and close the FFmpeg process."""
        if self.process is not None:
            self.process.stdin.close()
            self.process.wait()
            self.process = None
