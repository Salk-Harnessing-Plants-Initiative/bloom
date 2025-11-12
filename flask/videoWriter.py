import logging
import subprocess

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


class VideoWriter:
    def __init__(self, filename: str = "_autoplay.mp4", fps: float = 30.0) -> None:
        self.ffmpeg: subprocess.Popen | None = None
        self.filename = filename
        self.fps = fps

    def add(self, img: NDArray[np.uint8]) -> None:
        img_array = np.asarray(img)
        h, w = img_array.shape[:2]
        if self.ffmpeg is None:
            self.ffmpeg = self._open(w, h)
        if img_array.dtype in [np.float32, np.float64]:
            img_array = np.uint8(img_array.clip(0, 1) * 255)  # type: ignore[assignment]
        if len(img_array.shape) == 2:
            img_array = np.repeat(img_array[..., None], 3, -1)
        if self.ffmpeg.stdin:
            self.ffmpeg.stdin.write(img_array.tobytes())

    def _open(self, w: int, h: int) -> subprocess.Popen:
        if not isinstance(w, int) or not isinstance(h, int) or w <= 0 or h <= 0:
            raise ValueError(f"Invalid dimensions: {w}x{h}")
        if (not isinstance(self.fps, int | float)) or self.fps <= 0:
            raise ValueError(f"Invalid fps: {self.fps}")

        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "rawvideo",
            "-vcodec",
            "rawvideo",
            "-s",
            f"{w}x{h}",
            "-pix_fmt",
            "rgb24",
            "-r",
            str(self.fps),
            "-i",
            "-",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            self.filename,
        ]
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def close(self) -> None:
        if self.ffmpeg:
            try:
                if self.ffmpeg.stdin:
                    self.ffmpeg.stdin.close()
                returncode = self.ffmpeg.wait(timeout=30)
                if returncode != 0:
                    stderr = ""
                    if self.ffmpeg.stderr:
                        stderr = self.ffmpeg.stderr.read().decode()
                    raise RuntimeError(
                        f"ffmpeg failed with code {returncode}: {stderr}"
                    )
            except subprocess.TimeoutExpired as e:
                self.ffmpeg.kill()
                raise RuntimeError("ffmpeg process timed out after 30 seconds") from e
            finally:
                self.ffmpeg = None
