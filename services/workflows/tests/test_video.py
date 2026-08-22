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

    @property
    def not_(self):
        return self

    def is_(self, column, value):
        self.recorded.setdefault("not_is", []).append((column, value))
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


def test_get_scan_images_excludes_rows_with_no_object():
    """Filtered in the query, not after it.

    A row with no object cannot be encoded, so letting it through would spend a slot in the
    cap and push a real image at the tail outside the window — a permanently short video for
    a scan whose frames were all present. It would also make `frames_expected` count rows the
    recorded count never counts, so comparing the two would mean nothing.
    """
    c = _Client(result=[{"object_path": "a", "frame_number": 0}])
    video.get_scan_images(c, 5)
    assert ("object_path", "null") in c.q.recorded["not_is"]


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

    def create_signed_url(self, *a, **k):
        # Nothing stored for this scan, so the pre-encode gate lets the encode run — which
        # is the part these tests are about.
        raise RuntimeError("Object not found")


class _StorageClient:
    """Minimal client: storage.from_(bucket).download, and no recorded video."""

    def __init__(self, data=b""):
        self._bucket = _Bucket(data)

    def table(self, _name):
        return _GenQuery([])

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
        def __init__(self):
            self.uploads = 0

        def download(self, _path):
            return _png_bytes()

        def upload(self, *a, **k):
            self.uploads += 1

        def create_signed_url(self, *a, **k):
            return None  # storage couldn't sign a URL

    bucket = _UploadBucket()

    class _UploadClient:
        # A recorded count of 0, not an empty table: with nothing recorded the pre-encode gate
        # keeps the stored video and returns before encoding, so this would never reach the
        # upload whose signing failure it is written to test.
        def table(self, _name):
            return _GenQuery([{"frames": 0}])

        @property
        def storage(self):
            class _S:
                def from_(self, _name):
                    return bucket

            return _S()

    monkeypatch.setattr(video, "VideoWriter", _W)
    with pytest.raises(HTTPException) as ei:
        video.generate_scan_video(_UploadClient(), 5)
    assert ei.value.status_code == 500
    # The point of the test: the failure happens after the video was stored.
    assert bucket.uploads == 1


# --- I4 completeness signal + I5 re-encode guard (happy paths) ---------------

class _GenQuery:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    @property
    def not_(self):
        return self

    def is_(self, column, value):
        # Mirrors `.not_.is_(column, "null")`: the rows the query would not return.
        if value == "null":
            self._rows = [r for r in self._rows if r.get(column) is not None]
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


def test_rows_with_no_object_do_not_spend_slots_in_the_cap(monkeypatch):
    """Ten empty rows must not cost ten real angles.

    The window is `MAX_IMAGES + 1` rows ordered by frame_number. If unencodable rows travelled
    in it, a scan with images past them would encode short and — since a stored video is never
    replaced — stay short permanently, despite every frame having been present.
    """
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = (
        [{"object_path": None, "frame_number": i} for i in range(10)]
        + [{"object_path": f"o{i}", "frame_number": i} for i in range(10, 80)]
    )
    client = _GenClient(images)

    result = video.generate_scan_video(client, 5)

    # 70 real images, all inside the cap once the empty rows are excluded.
    assert result["frames"] == 70
    assert result["frames_expected"] == 70
    assert result["truncated"] is False


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


class _ExplodingWriter:
    """Any use at all is a failure: these tests assert nothing is encoded."""

    def __init__(self, *a, **k):
        raise AssertionError("encoded a video it had already decided to keep")


def test_a_video_this_run_cannot_beat_is_kept_without_encoding(monkeypatch):
    """The comparison is decidable up front: frames_written can never exceed frames_expected.

    Encoding first and discarding the result costs 72 downloads and an ffmpeg run per request,
    on every scan whose stored video already matches what the scan can supply.
    """
    monkeypatch.setattr(video, "VideoWriter", _ExplodingWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(3)]
    client = _GenClient(images, recorded_frames=3, stored=True)  # a tie, known in advance

    result = video.generate_scan_video(client, 5)

    assert result["regenerated"] is False
    assert result["frames"] == 3
    assert client.uploads == 0


def test_a_video_with_no_recorded_count_is_kept_without_encoding(monkeypatch):
    """The legacy case — nothing to compare against, so the encode could never be used."""
    monkeypatch.setattr(video, "VideoWriter", _ExplodingWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(3)]
    client = _GenClient(images, recorded_frames=None, stored=True)

    result = video.generate_scan_video(client, 5)

    assert result["regenerated"] is False
    assert result["stored_frames_unknown"] is True
    assert client.uploads == 0


def test_a_scan_that_might_beat_the_stored_video_still_encodes(monkeypatch):
    """The gate must not swallow the case it exists to let through."""
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(9)]
    client = _GenClient(images, recorded_frames=3, stored=True)  # 9 available vs 3 recorded

    result = video.generate_scan_video(client, 5)

    assert result["regenerated"] is True
    assert client.uploads == 1


def test_frames_lost_during_the_encode_fall_back_to_keeping(monkeypatch):
    """The pre-encode gate reasons about what is *available*; frames can still be lost after it.

    Nine rows against a recorded five clears the gate, but if five of the downloads fail the
    encode holds four — worse than what is stored. The rule is applied again on the real count,
    which is the only reason the post-encode comparison still exists.
    """
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(9)]
    client = _GenClient(images, recorded_frames=5, stored=True)

    def _flaky(self, path):
        if path in {"o0", "o1", "o2", "o3", "o4"}:
            raise RuntimeError("storage read failed")
        return _png_bytes()

    monkeypatch.setattr(_GenBucket, "download", _flaky)

    result = video.generate_scan_video(client, 5)

    assert result["regenerated"] is False   # 4 encoded vs 5 recorded -> keep
    assert result["frames"] == 5            # reports the stored video's count
    assert client.uploads == 0


def test_a_tie_after_the_gate_still_keeps_the_stored_video(monkeypatch):
    """The boundary the pre-encode gate cannot reach: equal counts decided *after* encoding.

    Nine rows against a recorded five clears the gate, so this encodes — and if four downloads
    fail it lands on exactly five, a tie. The same frame count is not the same frames, so the
    stored video is kept. Only `<=` gives that; `<` would overwrite.
    """
    monkeypatch.setattr(video, "VideoWriter", _FakeWriter)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(9)]
    client = _GenClient(images, recorded_frames=5, stored=True)

    def _flaky(self, path):
        if path in {"o0", "o1", "o2", "o3"}:
            raise RuntimeError("storage read failed")
        return _png_bytes()

    monkeypatch.setattr(_GenBucket, "download", _flaky)

    result = video.generate_scan_video(client, 5)

    assert result["regenerated"] is False  # 5 encoded vs 5 recorded -> a tie, so keep
    assert client.uploads == 0


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


def test_an_already_null_row_is_not_rewritten_on_every_request(monkeypatch):
    """A row that already says "unmeasured" is not made more unmeasured by writing it again.

    The first request records the path with no count; the second finds that row and leaves it
    alone. `frames` reads the same either way, which is why the row's existence is tracked
    separately — without it every request rewrites the same NULL over itself.
    """
    monkeypatch.setattr(video, "VideoWriter", _ExplodingWriter)
    monkeypatch.setattr(video, "scan_in_experiment", lambda *a, **k: True)
    images = [{"object_path": f"o{i}", "frame_number": i} for i in range(3)]

    writes: list = []
    monkeypatch.setattr(video, "_record_video", lambda c, s, r: writes.append(r))

    # First request: nothing recorded yet, so the path is written with no count.
    first = _GenClient(images, recorded_frames=None, stored=True)
    monkeypatch.setattr(video, "app_client", lambda: first)
    video.generate_experiment_scan_video(1, 5)
    assert len(writes) == 1
    assert writes[0]["frames"] is None

    # Second request: the row is there, still with a NULL count. Nothing more to say.
    second = _GenClient(images, recorded_frames=None, stored=True)
    second._recorded = [{"frames": None}]  # a row exists; its count is unknown
    monkeypatch.setattr(video, "app_client", lambda: second)
    result = video.generate_experiment_scan_video(1, 5)

    assert result["regenerated"] is False
    assert second.uploads == 0
    assert len(writes) == 1, "the existing NULL row was rewritten"


def test_a_failed_record_lookup_does_not_read_as_no_record(monkeypatch):
    """"Nothing recorded" sends the request down the unmeasured-video branch, where the stored
    video is kept whatever was encoded and a null count is written over the real one. An error
    must not be reported as that."""
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
