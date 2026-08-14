"""Unit tests for the video module's DB-query + record logic (no ffmpeg,
storage, or supabase client needed — a fake fluent client is used)."""

import io
import threading
import time

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

    def rpc(self, name, params):
        self.recorded["rpc"] = {"name": name, "params": params}
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
        return self.q.rpc(name, params)


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


def test_record_video_calls_the_wrapper_with_scan_and_path(monkeypatch):
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


def test_record_video_never_upserts_the_table_directly():
    """An upsert writes scan_id into the SET clause, which bloom_workflows cannot update."""
    c = _Client()
    video._record_video(c, 5, {"path": "cyl-videos/5.mp4", "frames": 72})
    assert "upsert" not in c.q.recorded


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
        # A real client answers the record lookup; a lookup that errors is now a 503, so a
        # fake without this would fail here instead of reaching the case under test.
        def table(self, _name):
            return _GenQuery([])

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

    def limit(self, n=None):
        # Honoured, not ignored: truncation is detected by asking for one row past the cap,
        # so a fake that returns everything regardless proves nothing about that boundary.
        if n is not None:
            self._rows = self._rows[:n]
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
        self._outer.stored = True

    def create_signed_url(self, *a, **k):
        # Storage answers for a missing object with an error, not an empty result — which
        # is what makes signing usable as an existence check.
        if not self._outer.stored:
            raise RuntimeError("Object not found")
        return "http://signed"


class _GenClient:
    """Table (cyl_images + cyl_scan_videos) + storage, for full generate runs."""

    def __init__(self, images, recorded_frames=None, stored=None):
        self._images = images
        self._recorded = [] if recorded_frames is None else [{"frames": recorded_frames}]
        self.uploads = 0
        # Whether an object sits at this scan's video key. A recorded count implies one.
        self.stored = recorded_frames is not None if stored is None else stored

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


def test_two_requests_for_one_scan_do_not_overlap(monkeypatch):
    """Without the lock both read "nothing recorded" and the loser's video wins."""
    monkeypatch.setattr(video, "scan_in_experiment", lambda *a, **k: True)
    monkeypatch.setattr(video, "app_client", lambda: object())
    monkeypatch.setattr(video, "_record_video", lambda *a, **k: None)

    inside = 0
    overlapped = False

    def _slow(_client, scan_id):
        nonlocal inside, overlapped
        inside += 1
        if inside > 1:
            overlapped = True
        time.sleep(0.05)
        inside -= 1
        return {"regenerated": False}

    monkeypatch.setattr(video, "generate_scan_video", _slow)

    threads = [
        threading.Thread(target=video.generate_experiment_scan_video, args=(1, 5))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert overlapped is False


def test_two_scans_still_encode_in_parallel(monkeypatch):
    assert video._scan_lock(1) is not video._scan_lock(2)
    assert video._scan_lock(1) is video._scan_lock(1)


def test_generate_scan_video_keeps_a_stored_video_it_cannot_compare(monkeypatch):
    """A video predating `cyl_scan_videos` has no recorded count, so it is kept."""
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(3)]
    client = _GenClient(images, recorded_frames=None, stored=True)

    result = video.generate_scan_video(client, 5)

    assert result["regenerated"] is False
    assert client.uploads == 0


def test_an_unclear_existence_check_is_neither_yes_nor_no():
    class _Unreachable:
        def create_signed_url(self, *a, **k):
            raise RuntimeError("storage gateway timed out")

    assert video._stored_video_exists(_Unreachable(), "cyl-videos/5.mp4") is None


def _blip_signing(monkeypatch):
    """Make every signing attempt fail for a reason that is not "missing"."""
    def _blip(self, *a, **k):
        raise RuntimeError("storage gateway timed out")

    monkeypatch.setattr(_GenBucket, "create_signed_url", _blip)


def test_an_unclear_existence_check_never_overwrites(monkeypatch):
    """The outage that clouds the probe is the one that makes this encode the worse one.

    Frame downloads and the existence probe fail together, so an unclear answer arrives
    exactly when this run has the fewest frames. Treating it as "nothing is stored" would
    replace a full rotation with a fragment, in place, on a bucket with no versioning and
    with no way back. It refuses instead.
    """
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": "o0", "frame_number": 0}]
    client = _GenClient(images, recorded_frames=72)  # a far better video is recorded
    _blip_signing(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        video.generate_scan_video(client, 5)

    assert excinfo.value.status_code == 503
    assert client.uploads == 0  # the stored 72-frame video is untouched


def test_an_unclear_existence_check_refuses_rather_than_keeping(monkeypatch):
    """Nor does it keep: `_result` would sign the key the probe just failed to sign."""
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(3)]
    client = _GenClient(images, recorded_frames=3)
    _blip_signing(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        video.generate_scan_video(client, 5)

    assert excinfo.value.status_code == 503
    assert client.uploads == 0


def test_exactly_at_the_cap_is_not_truncated(monkeypatch):
    """72 is the cap, not past it — the over-fetch of one row exists to tell those apart."""
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(video.MAX_IMAGES)]
    result = video.generate_scan_video(_GenClient(images), 5)

    assert result["truncated"] is False
    assert result["frames"] == video.MAX_IMAGES


def test_one_past_the_cap_is_truncated(monkeypatch):
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(video.MAX_IMAGES + 1)]
    result = video.generate_scan_video(_GenClient(images), 5)

    assert result["truncated"] is True
    assert result["frames"] == video.MAX_IMAGES


def test_a_strictly_better_encode_replaces_the_stored_video(monkeypatch):
    """The other half of the rule. Without it a scan is frozen on its worst encode forever."""
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(9)]
    client = _GenClient(images, recorded_frames=3, stored=True)  # 9 frames now vs 3 recorded

    result = video.generate_scan_video(client, 5)

    assert result["regenerated"] is True
    assert result["frames"] == 9
    assert client.uploads == 1


def test_a_kept_video_is_never_re_recorded(monkeypatch):
    """Recording a kept result would write the discarded encode's lower count over the real one."""
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    monkeypatch.setattr(video, "scan_in_experiment", lambda *a, **k: True)
    images = [{"object_path": "o0", "frame_number": 0}]          # this run manages 1 frame
    client = _GenClient(images, recorded_frames=72, stored=True)  # a 72-frame video is recorded
    monkeypatch.setattr(video, "app_client", lambda: client)

    calls: list = []
    monkeypatch.setattr(video, "_record_video", lambda c, s, r: calls.append(r))

    result = video.generate_experiment_scan_video(1, 5)

    assert result["regenerated"] is False
    assert client.uploads == 0
    assert calls == [], "a kept video must not have its recorded frame count rewritten"


def test_a_failed_record_lookup_does_not_read_as_no_record(monkeypatch):
    """"Nothing recorded" is what permits an overwrite, so an error must not report it."""
    monkeypatch.setattr(video, "VIDEO_TABLE", "cyl_scan_videos")

    class _Broken:
        def table(self, _name):
            raise RuntimeError("PostgREST unavailable")

    with pytest.raises(HTTPException) as excinfo:
        video._recorded_frames(_Broken(), 5)

    assert excinfo.value.status_code == 503


def test_a_kept_video_with_no_row_is_recorded_without_a_count(monkeypatch):
    """A stored video nothing records can never be compared against, so it is pinned here.

    The keep branch returns regenerated=False, which normally suppresses the record write —
    and the row it would have written is the only thing that lets a later run evaluate this
    scan at all. Recording the path with a null count claims nothing and breaks the loop.
    """
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    monkeypatch.setattr(video, "scan_in_experiment", lambda *a, **k: True)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(3)]
    client = _GenClient(images, recorded_frames=None, stored=True)
    monkeypatch.setattr(video, "app_client", lambda: client)

    recorded: dict = {}
    monkeypatch.setattr(
        video, "_record_video", lambda c, s, r: recorded.update(scan=s, result=r)
    )

    result = video.generate_experiment_scan_video(1, 5)

    assert result["regenerated"] is False
    assert client.uploads == 0
    assert recorded["scan"] == 5
    assert recorded["result"]["frames"] is None
    assert recorded["result"]["path"] == "cyl-videos/5.mp4"
    # The response still carries a number, because the client's shape guard demands one.
    assert isinstance(result["frames"], int)
    assert "stored_frames_unknown" not in result


def test_generate_scan_video_replaces_a_recorded_video_whose_object_is_gone(monkeypatch):
    """A row says a video was stored once, not that it still is.

    Trusting the row here would sign a key with nothing behind it, which raises — failing the
    request after the encode had already been paid for, and leaving the scan permanently
    unable to produce a video.
    """
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(3)]
    client = _GenClient(images, recorded_frames=3, stored=False)

    result = video.generate_scan_video(client, 5)

    assert result["regenerated"] is True
    assert client.uploads == 1


def test_generate_scan_video_keeps_an_equal_existing(monkeypatch):
    """A tie is not an improvement, and the request is one any signed-in user can make.

    Equal counts do not mean equal frames: a row that finished uploading and a row that
    became unreadable cancel out, so the same number can describe a different rotation.
    Overwriting is in-place on a bucket with no versioning, so there is no way back.
    """
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(3)]
    client = _GenClient(images, recorded_frames=3)

    result = video.generate_scan_video(client, 5)

    assert result["regenerated"] is False
    assert client.uploads == 0


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
