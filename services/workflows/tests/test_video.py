"""Unit tests for the video module's DB-query + record logic (no ffmpeg,
storage, or supabase client needed — a fake fluent client is used)."""

import io

import pytest
from fastapi import HTTPException
from PIL import Image

import video
from video_writer import VideoEncodeError


class _Query:
    def __init__(self, result):
        self._result = result
        self.recorded: dict = {}

    def select(self, *a, **k):
        return self

    def eq(self, key, val):
        self.recorded.setdefault("eq", []).append((key, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self.recorded["limit"] = n
        return self

    def upsert(self, payload, on_conflict=None):
        self.recorded["upsert"] = {"payload": payload, "on_conflict": on_conflict}
        return self

    def execute(self):
        class _R:
            pass

        r = _R()
        r.data = self._result
        return r


class _Client:
    def __init__(self, result=None):
        self.q = _Query(result if result is not None else [])
        self.last_table = None

    def table(self, name):
        self.last_table = name
        return self.q


def test_scan_in_experiment_true():
    c = _Client(result=[{"scan_id": 5}])
    assert video.scan_in_experiment(c, 1, 5) is True
    assert ("experiment_id", 1) in c.q.recorded["eq"]
    assert ("scan_id", 5) in c.q.recorded["eq"]


def test_scan_in_experiment_false():
    c = _Client(result=[])
    assert video.scan_in_experiment(c, 1, 5) is False


def test_get_scan_images_capped_at_max():
    c = _Client(result=[{"object_path": "a", "frame_number": 0}])
    video.get_scan_images(c, 5)
    assert c.q.recorded["limit"] == video.MAX_IMAGES == 72


def test_record_video_upserts_scan_and_path(monkeypatch):
    monkeypatch.setattr(video, "VIDEO_TABLE", "cyl_scan_videos")
    c = _Client()
    video._record_video(c, 5, {"path": "cyl-videos/5.mp4", "frames": 72})
    up = c.q.recorded["upsert"]
    assert up["payload"] == {"scan_id": 5, "path": "cyl-videos/5.mp4"}
    assert up["on_conflict"] == "scan_id"
    assert c.last_table == "cyl_scan_videos"


def test_record_video_skipped_when_table_unset(monkeypatch):
    monkeypatch.setattr(video, "VIDEO_TABLE", None)
    c = _Client()
    video._record_video(c, 5, {"path": "cyl-videos/5.mp4", "frames": 72})
    assert "upsert" not in c.q.recorded


# --- generate_scan_video failure paths (never upload a bad/absent video) -----

def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    return buf.getvalue()


class _Bucket:
    def __init__(self, data):
        self._data = data

    def download(self, _path):
        return self._data


class _StorageClient:
    """Minimal client exposing only storage.from_(bucket).download."""

    def __init__(self, data=b""):
        self._bucket = _Bucket(data)

    @property
    def storage(self):
        outer = self

        class _S:
            def from_(self, _name):
                return outer._bucket

        return _S()


def _one_image(_client, _scan_id):
    return [{"object_path": "a", "frame_number": 0}]


def test_generate_scan_video_404_when_no_images(monkeypatch):
    monkeypatch.setattr(video, "get_scan_images", lambda c, s: [])
    with pytest.raises(HTTPException) as ei:
        video.generate_scan_video(_StorageClient(), 5)
    assert ei.value.status_code == 404


def test_generate_scan_video_500_when_no_frames_encoded(monkeypatch):
    monkeypatch.setattr(video, "get_scan_images", _one_image)

    class _W:
        def __init__(self, filename, fps=30.0):
            pass

        def add(self, _arr):
            raise RuntimeError("undecodable frame")  # every frame skipped

        def close(self, timeout=120.0):
            pass

    monkeypatch.setattr(video, "VideoWriter", _W)
    with pytest.raises(HTTPException) as ei:
        video.generate_scan_video(_StorageClient(_png_bytes()), 5)
    assert ei.value.status_code == 500


def test_generate_scan_video_500_on_encode_failure(monkeypatch):
    monkeypatch.setattr(video, "get_scan_images", _one_image)

    class _W:
        def __init__(self, filename, fps=30.0):
            pass

        def add(self, _arr):
            pass

        def close(self, timeout=120.0):
            raise VideoEncodeError("ffmpeg exited 1: boom")

    monkeypatch.setattr(video, "VideoWriter", _W)
    with pytest.raises(HTTPException) as ei:
        video.generate_scan_video(_StorageClient(_png_bytes()), 5)
    assert ei.value.status_code == 500


def test_generate_scan_video_500_on_empty_output(monkeypatch):
    monkeypatch.setattr(video, "get_scan_images", _one_image)

    class _W:  # encodes "successfully" but never writes the file
        def __init__(self, filename, fps=30.0):
            pass

        def add(self, _arr):
            pass

        def close(self, timeout=120.0):
            pass

    monkeypatch.setattr(video, "VideoWriter", _W)
    with pytest.raises(HTTPException) as ei:
        video.generate_scan_video(_StorageClient(_png_bytes()), 5)
    assert ei.value.status_code == 500
