import subprocess

import numpy as np


class VideoWriter:
    def __init__(self, filename="_autoplay.mp4", fps=30.0):
        self.ffmpeg = None
        self.filename = filename
        self.fps = fps

    def add(self, img):
        img = np.asarray(img)
        h, w = img.shape[:2]
        if self.ffmpeg is None:
            self.ffmpeg = self._open(w, h)
        if img.dtype in [np.float32, np.float64]:
            img = np.uint8(img.clip(0, 1) * 255)
        if len(img.shape) == 2:
            img = np.repeat(img[..., None], 3, -1)
        self.ffmpeg.stdin.write(img.tobytes())

    def _open(self, w, h):
        cmd = f"""ffmpeg -y -f rawvideo -vcodec rawvideo -s {w}x{h}
        -pix_fmt rgb24 -r {self.fps} -i - -pix_fmt yuv420p
        -c:v libx264 -crf 20 {self.filename}""".split()
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def close(self):
        if self.ffmpeg:
            self.ffmpeg.stdin.close()
            self.ffmpeg.wait()
            self.ffmpeg = None
