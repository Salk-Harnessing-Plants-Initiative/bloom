"""
VideoWriter - FFmpeg-based video encoding utility

Streams images to FFmpeg subprocess for efficient video encoding.
"""

import subprocess
import numpy as np


class VideoWriter:
    """Write video frames to file using FFmpeg."""

    def __init__(self, filename: str, fps: float = 30.0):
        """
        Initialize video writer.

        Args:
            filename: Output video file path
            fps: Frames per second (default: 30.0)
        """
        self.filename = filename
        self.fps = fps
        self.process = None
        self.width = None
        self.height = None

    def add(self, img: np.ndarray):
        """
        Add a frame to the video.

        Args:
            img: numpy array of shape (height, width, channels) or (height, width)
        """
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
        """
        Open FFmpeg process for writing.

        Args:
            width: Video width
            height: Video height
        """
        self.width = width
        self.height = height

        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output file
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', f'{width}x{height}',
            '-pix_fmt', 'rgb24',
            '-r', str(self.fps),
            '-i', '-',  # Read from stdin
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            self.filename
        ]

        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def close(self):
        """Finalize video and close FFmpeg process."""
        if self.process is not None:
            self.process.stdin.close()
            self.process.wait()
            self.process = None
