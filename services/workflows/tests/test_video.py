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

    def rpc(self, name, params):
        self.q.recorded["rpc"] = {"name": name, "params": params}
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


def test_record_video_goes_through_the_wrapper(monkeypatch):
    """Not a client upsert: PostgREST's merge-duplicates writes every payload key, and
    bloom_workflows may update path/frames but never scan_id — so the upsert was refused
    outright and no video was ever recorded."""
    monkeypatch.setattr(video, "VIDEO_TABLE", "cyl_scan_videos")
    c = _Client()
    video._record_video(c, 5, {"path": "cyl-videos/5.mp4", "frames": 72})
    call = c.q.recorded["rpc"]
    assert call["name"] == "record_cyl_scan_video"
    assert call["params"] == {
        "p_scan_id": 5,
        "p_path": "cyl-videos/5.mp4",
        "p_frames": 72,
    }
    assert "upsert" not in c.q.recorded, "the upsert is what could not write scan_id"


def test_record_video_skipped_when_table_unset(monkeypatch):
    monkeypatch.setattr(video, "VIDEO_TABLE", None)
    c = _Client()
    video._record_video(c, 5, {"path": "cyl-videos/5.mp4", "frames": 72})
    assert "rpc" not in c.q.recorded


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


def _one_image(_client, _scan_id, _limit=None):
    return [{"object_path": "a", "frame_number": 0}]


def test_generate_scan_video_404_when_no_images(monkeypatch):
    monkeypatch.setattr(video, "get_scan_images", lambda c, s, limit=None: [])
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


def test_generate_scan_video_500_when_no_signed_url(monkeypatch):
    monkeypatch.setattr(video, "get_scan_images", _one_image)

    class _W:  # writes a real, non-empty file so the empty-output guard passes
        def __init__(self, filename, fps=30.0):
            self._filename = filename

        def add(self, _arr):
            pass

        def close(self, timeout=120.0):
            with open(self._filename, "wb") as fh:
                fh.write(b"\x00\x01")

    class _UploadBucket:
        def download(self, _path):
            return _png_bytes()

        def upload(self, *a, **k):
            pass

        def create_signed_url(self, *a, **k):
            return None  # storage couldn't sign a URL

    class _UploadClient:
        @property
        def storage(self):
            bucket = _UploadBucket()

            class _S:
                def from_(self, _name):
                    return bucket

            return _S()

    monkeypatch.setattr(video, "VideoWriter", _W)
    with pytest.raises(HTTPException) as ei:
        video.generate_scan_video(_UploadClient(), 5)
    assert ei.value.status_code == 500


# --- I4 completeness signal + I5 re-encode guard (happy paths) ---------------

class _GenQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        class _R:
            pass

        r = _R()
        r.data = self._rows
        return r


class _GenBucket:
    def __init__(self, outer):
        self._outer = outer

    def download(self, _path):
        return _png_bytes()

    def upload(self, *a, **k):
        self._outer.uploads += 1

    def create_signed_url(self, *a, **k):
        return "http://signed"


class _GenClient:
    """Table (cyl_images + cyl_scan_videos) + storage, for full generate runs."""

    def __init__(self, images, recorded_frames=None):
        self._images = images
        self._recorded = [] if recorded_frames is None else [{"frames": recorded_frames}]
        self.uploads = 0

    def table(self, name):
        if name == "cyl_images":
            return _GenQuery(self._images)
        if name == "cyl_scan_videos":
            return _GenQuery(self._recorded)
        return _GenQuery([])

    @property
    def storage(self):
        outer = self

        class _S:
            def from_(self, _name):
                return _GenBucket(outer)

        return _S()


class _FakeWriter:
    def __init__(self, filename, fps=30.0):
        self._filename = filename

    def add(self, _arr):
        pass

    def close(self, timeout=120.0):
        with open(self._filename, "wb") as fh:
            fh.write(b"\x00\x01")


def test_generate_scan_video_reports_completeness(monkeypatch):
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(3)]
    client = _GenClient(images)

    result = video.generate_scan_video(client, 5)

    assert result["frames"] == 3
    assert result["frames_expected"] == 3
    assert result["truncated"] is False
    assert result["regenerated"] is True
    assert client.uploads == 1


def test_generate_scan_video_flags_truncation(monkeypatch):
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [
        {"object_path": f"o{i}", "frame_number": i}
        for i in range(video.MAX_IMAGES + 5)
    ]
    client = _GenClient(images)

    result = video.generate_scan_video(client, 5)

    assert result["truncated"] is True
    assert result["frames_expected"] == video.MAX_IMAGES
    assert result["frames"] == video.MAX_IMAGES


def test_generate_scan_video_keeps_better_existing(monkeypatch):
    # A prior video has 72 frames; this run manages only 1 -> keep the old one.
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": "o0", "frame_number": 0}]
    client = _GenClient(images, recorded_frames=72)

    result = video.generate_scan_video(client, 5)

    assert result["regenerated"] is False
    assert result["frames"] == 72          # reports the kept video's count
    assert client.uploads == 0             # did not overwrite


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
