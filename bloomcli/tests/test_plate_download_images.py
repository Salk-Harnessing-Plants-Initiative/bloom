"""`bloomctl plate download` — queries, the download loop, resume and failure handling.

A plate scan holds exactly one image, so the loop is one-image-per-scan rather than the
cylinder's many-frames-per-scan. Resume can verify size here, because gravi_images records
file_size_bytes where cyl_images does not.
"""

from __future__ import annotations

import os
import threading
import time

import pytest
from test_plate_download_paths import SCAN

import bloomctl.plate.download as pd
from bloomctl._postgrest import ID_FILTER_BUDGET_CHARS


class _Bucket:
    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.requested = []

    def download(self, object_path):
        self.requested.append(object_path)
        if object_path in self.fail_on:
            raise RuntimeError("500 storage error")
        return f"bytes::{object_path}".encode()


class _Client:
    """Stand-in client that records which bucket was asked for."""

    def __init__(self, fail_on=()):
        self.bucket = _Bucket(fail_on)
        self.buckets_asked = []
        client = self

        class _Storage:
            def from_(self, name):
                client.buckets_asked.append(name)
                return client.bucket

        self.storage = _Storage()


def _scan(scan_id, plate_id, **overrides):
    return {**SCAN, "scan_id": scan_id, "plate_id": plate_id, **overrides}


def _image(scan_id, size=2048, name=None):
    return {
        "scan_id": scan_id,
        "object_path": name or f"gravi/{scan_id}.jpg",
        "file_size_bytes": size,
    }


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #


class _Query:
    """Records the PostgREST filter chain so the tests can assert on it."""

    def __init__(self, recorder, rows):
        self.recorder = recorder
        self.rows = rows

    def select(self, *a):
        return self

    def eq(self, column, value):
        self.recorder.setdefault("eq", []).append((column, value))
        return self

    def in_(self, column, values):
        self.recorder.setdefault("in", []).append((column, list(values)))
        return self

    def order(self, column):
        self.recorder["order"] = column
        return self

    def limit(self, n):
        self.recorder["limit"] = n
        return self

    def execute(self):
        return type("R", (), {"data": self.rows})()


class _TableClient:
    def __init__(self, rows=None):
        self.calls = {}
        self.rows = rows or []

    def table(self, name):
        self.calls["table"] = name
        return _Query(self.calls, self.rows)

    def rpc(self, name, params):
        self.calls["rpc"] = (name, params)
        return type("R", (), {"execute": lambda _self: type("D", (), {"data": self.rows})()})()


def test_fetch_plate_scans_reads_the_extended_view():
    client = _TableClient([SCAN])
    pd.fetch_plate_scans(client, 12)
    assert client.calls["table"] == "gravi_scans_extended"
    assert ("experiment_id", 12) in client.calls["eq"]


def test_a_sample_is_the_same_sample_every_time():
    """`--limit` is for looking at part of an experiment, so it has to be reproducible.

    Without an ORDER BY the rows arrive in whatever order the plan produces. That is stable in
    practice and was stable in testing, but it is incidental — and a sample that can quietly
    differ between runs is one a scientist cannot compare against itself.
    """
    client = _TableClient([])
    pd.fetch_plate_scans(client, 12, limit=50)
    assert client.calls["order"] == "scan_id"


def test_fetch_plate_scans_applies_every_filter():
    client = _TableClient([])
    pd.fetch_plate_scans(client, 12, plate_id="P1", wave_number=3, session_id=88, limit=50)
    applied = dict(client.calls["eq"])
    assert applied["plate_id"] == "P1"
    assert applied["wave_number"] == 3
    assert applied["session_id"] == 88
    assert client.calls["limit"] == 50


def test_fetch_plate_scans_omits_filters_that_were_not_given():
    client = _TableClient([])
    pd.fetch_plate_scans(client, 12)
    assert [c for c, _ in client.calls["eq"]] == ["experiment_id"]


def test_fetch_plate_images_maps_scan_id_to_its_single_image():
    rows = [_image(1), _image(2)]
    client = _TableClient(rows)
    found = pd.fetch_plate_images(client, [1, 2])
    assert client.calls["table"] == "gravi_images"
    assert found[1]["object_path"] == "gravi/1.jpg"
    assert found[2]["object_path"] == "gravi/2.jpg"


def test_fetch_plate_images_is_one_query_for_a_small_selection():
    # One image per scan means a whole batch comes back in a single `in` filter, rather than
    # the cylinder's per-scan listing round-trip.
    client = _TableClient([])
    pd.fetch_plate_images(client, [1, 2, 3])
    assert client.calls["in"] == [("scan_id", [1, 2, 3])]


def test_fetch_plate_images_batches_so_the_url_cannot_get_too_long():
    # A PostgREST `in.(…)` filter travels in the URL. Sending every scan id at once returns
    # 414 URI Too Long past roughly 1,300 ids — which a continuous session passes easily.
    client = _TableClient([])
    pd.fetch_plate_images(client, list(range(1, 5001)))

    batches = [ids for column, ids in client.calls["in"] if column == "scan_id"]
    assert len(batches) > 1, "5,000 scan ids must not go out as one filter"
    assert sum(len(b) for b in batches) == 5000, "every id must still be requested"
    assert [i for b in batches for i in b] == list(range(1, 5001)), "no id lost or reordered"
    for batch in batches:
        assert len(",".join(map(str, batch))) <= ID_FILTER_BUDGET_CHARS



def test_no_scan_ids_makes_no_query():
    client = _TableClient([])
    assert pd.fetch_plate_images(client, []) == {}
    assert "in" not in client.calls


class _SectionClient:
    """Serves sections and their plants from two tables, like the real schema."""

    def __init__(self, sections, plants):
        self.sections, self.plants = sections, plants

    def table(self, name):
        rows = self.sections if name == "gravi_scan_metadata_sections" else self.plants
        return _Query({}, rows)


def test_a_section_with_no_plants_keeps_its_medium():
    # The growth condition is a property of the section, not of the plants in it. Dropping a
    # plantless section would lose the medium — and medium is what analyses group by.
    client = _SectionClient(
        sections=[
            {"id": 1, "metadata_id": 55, "plate_section_id": "top", "medium": "MS"},
            {"id": 2, "metadata_id": 55, "plate_section_id": "empty", "medium": "MS+NaCl"},
        ],
        plants=[{"section_id": 1, "plant_qr": "QR-1"}],
    )
    rows = pd.fetch_plate_sections(client, [55])

    by_section = {r["plate_section_id"]: r for r in rows}
    assert set(by_section) == {"top", "empty"}
    assert by_section["empty"]["medium"] == "MS+NaCl"
    assert by_section["empty"]["plant_qr"] == "", "no plant recorded, so the column is empty"


def test_a_section_with_several_plants_gets_a_row_each():
    client = _SectionClient(
        sections=[{"id": 1, "metadata_id": 55, "plate_section_id": "top", "medium": "MS"}],
        plants=[{"section_id": 1, "plant_qr": "QR-1"}, {"section_id": 1, "plant_qr": "QR-2"}],
    )
    rows = pd.fetch_plate_sections(client, [55])
    assert sorted(r["plant_qr"] for r in rows) == ["QR-1", "QR-2"]
    assert all(r["medium"] == "MS" for r in rows)


def test_fetch_plate_sections_batches_too():
    client = _TableClient([])
    pd.fetch_plate_sections(client, list(range(1, 5001)))
    metadata_batches = [ids for column, ids in client.calls["in"] if column == "metadata_id"]
    assert len(metadata_batches) > 1


def test_search_plate_experiments_calls_the_gravi_rpc():
    client = _TableClient([])
    pd.search_experiments(client, "gravi", species="Pennycress")
    name, params = client.calls["rpc"]
    assert name == "gravi_experiment_search"
    assert params["p_query"] == "gravi" and params["p_species"] == "Pennycress"


# --------------------------------------------------------------------------- #
# Downloading
# --------------------------------------------------------------------------- #


def test_download_reads_the_graviscan_bucket_never_the_cyl_one(tmp_path):
    client = _Client()
    pd.download_images(client, [_scan(1, "P1")], {1: _image(1)}, tmp_path)
    assert client.buckets_asked == ["graviscan-images"]


def test_each_scan_writes_exactly_one_image(tmp_path):
    client = _Client()
    scans = [_scan(1, "P1"), _scan(2, "P2")]
    result = pd.download_images(client, scans, {1: _image(1), 2: _image(2)}, tmp_path)

    assert result.ok == 2 and result.total == 2 and result.failed == 0
    written = sorted(p.name for p in (tmp_path / "images").rglob("*.jpg"))
    assert len(written) == 2


def test_a_scan_with_no_image_row_is_noted_but_does_not_fail_the_run(tmp_path):
    client = _Client()
    result = pd.download_images(client, [_scan(1, "P1"), _scan(2, "P2")], {1: _image(1)}, tmp_path)

    assert result.scans_without_frames == 1
    assert result.failed == 0
    assert result.incomplete is False, "nothing to fetch is not a failure"


def test_a_failing_object_is_recorded_and_the_run_continues(tmp_path):
    client = _Client(fail_on={"gravi/2.jpg"})
    scans = [_scan(1, "P1"), _scan(2, "P2"), _scan(3, "P3")]
    images = {1: _image(1), 2: _image(2), 3: _image(3)}

    result = pd.download_images(client, scans, images, tmp_path)

    assert result.failed == 1 and result.ok == 2
    assert result.incomplete is True


def test_malformed_image_row_is_reported_against_gravi_images(tmp_path):
    client = _Client()
    result = pd.download_images(client, [_scan(1, "P1")], {1: {"scan_id": 1}}, tmp_path)
    assert result.failed == 1
    assert "gravi_images" in result.frames[0].error


def test_results_stay_in_scan_order(tmp_path):
    client = _Client()
    scans = [_scan(i, f"P{i}") for i in range(1, 6)]
    images = {i: _image(i) for i in range(1, 6)}
    result = pd.download_images(client, scans, images, tmp_path, workers=4)
    assert [f.scan_id for f in result.frames] == [1, 2, 3, 4, 5]


def test_sequential_mode_downloads_one_at_a_time(tmp_path):
    client = _Client()
    scans = [_scan(i, f"P{i}") for i in range(1, 4)]
    images = {i: _image(i) for i in range(1, 4)}
    result = pd.download_images(client, scans, images, tmp_path, workers=1)
    assert result.ok == 3
    assert client.bucket.requested == ["gravi/1.jpg", "gravi/2.jpg", "gravi/3.jpg"]


class _CountingBucket(_Bucket):
    """Blocks each download until released, recording the high-water mark of concurrent calls."""

    def __init__(self, release: threading.Event):
        super().__init__()
        self.release = release
        self.in_flight = 0
        self.peak = 0
        self.lock = threading.Lock()

    def download(self, object_path):
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        # Hold the call open so overlapping downloads are actually simultaneous rather than
        # merely fast — without this, workers could run one at a time and still pass.
        self.release.wait(timeout=5)
        with self.lock:
            self.in_flight -= 1
        return f"bytes::{object_path}".encode()


def test_captures_really_download_concurrently(tmp_path):
    # The point of --workers: several captures in flight at once. A large plate experiment is
    # thousands of one-request-each downloads, so overlapping the waiting is what makes it
    # finish in a sensible time.
    release = threading.Event()
    client = _Client()
    client.bucket = _CountingBucket(release)

    scans = [_scan(i, f"P{i}") for i in range(1, 9)]
    images = {i: _image(i) for i in range(1, 9)}

    watcher = threading.Thread(target=lambda: (time.sleep(0.3), release.set()))
    watcher.start()
    result = pd.download_images(client, scans, images, tmp_path, workers=4)
    watcher.join()

    assert result.ok == 8
    assert client.bucket.peak > 1, "captures downloaded one at a time despite --workers 4"
    assert client.bucket.peak <= 4, f"more than --workers in flight: {client.bucket.peak}"


def test_worker_count_never_exceeds_the_number_of_captures(tmp_path):
    # Two captures must not start four threads.
    release = threading.Event()
    release.set()
    client = _Client()
    client.bucket = _CountingBucket(release)

    result = pd.download_images(
        client, [_scan(1, "P1"), _scan(2, "P2")], {1: _image(1), 2: _image(2)}, tmp_path, workers=8
    )

    assert result.ok == 2
    assert client.bucket.peak <= 2


def test_progress_reports_captures_not_frames(tmp_path, capsys):
    """A plate run counts captures. Calling them frames describes the wrong experiment.

    The callback carries no noun — it lives in ProgressReporter — so a test that passes a
    lambda cannot see the word at all. This drives the reporter the CLI actually uses and
    reads the line it prints.
    """
    import bloomctl._download as shared

    seen = []
    report = shared.ProgressReporter(interval=0.0, noun=f"{pd.NOUN}s")

    def _both(phase, done, total, failed=0):
        seen.append((phase, done, total, failed))
        report(phase, done, total, failed)

    pd.download_images(
        _Client(), [_scan(1, "P1")], {1: _image(1)}, tmp_path, on_progress=_both
    )

    assert seen, "progress must be reported"
    assert seen[-1][0] == "downloading"
    printed = capsys.readouterr().err
    assert "captures" in printed, f"progress called them something else: {printed!r}"
    assert "frames" not in printed


# --------------------------------------------------------------------------- #
# Resume — the plate side can verify size, unlike the cylinder side
# --------------------------------------------------------------------------- #


def test_an_image_of_the_recorded_size_is_skipped(tmp_path):
    client = _Client()
    scan, image = _scan(1, "P1"), _image(1, size=len(b"bytes::gravi/1.jpg"))
    pd.download_images(client, [scan], {1: image}, tmp_path)
    client.bucket.requested.clear()

    result = pd.download_images(client, [scan], {1: image}, tmp_path)

    assert result.skipped == 1 and result.ok == 1
    assert client.bucket.requested == [], "a complete image must not be re-requested"


def test_a_truncated_image_is_downloaded_again(tmp_path):
    client = _Client()
    scan, image = _scan(1, "P1"), _image(1, size=9999)
    dest = pd.image_dest(tmp_path, scan, image)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"short")

    result = pd.download_images(client, [scan], {1: image}, tmp_path)

    assert result.skipped == 0 and result.downloaded == 1
    assert dest.read_bytes() == b"bytes::gravi/1.jpg"


def test_a_null_recorded_size_falls_back_to_non_empty(tmp_path):
    client = _Client()
    scan, image = _scan(1, "P1"), _image(1, size=None)
    dest = pd.image_dest(tmp_path, scan, image)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"anything")

    result = pd.download_images(client, [scan], {1: image}, tmp_path)

    assert result.skipped == 1
    assert client.bucket.requested == []


def test_a_wrong_recorded_size_is_noted_so_endless_re_downloads_are_diagnosable(tmp_path):
    # If file_size_bytes disagrees with what storage serves, the resume check can never be
    # satisfied and every run re-fetches. The bytes are fine, so it isn't a failure — but
    # without a note the user sees an experiment re-download forever with no explanation.
    client = _Client()
    scan, image = _scan(1, "P1"), _image(1, size=9999)

    result = pd.download_images(client, [scan], {1: image}, tmp_path)

    assert result.ok == 1 and result.failed == 0
    note = result.frames[0].note
    assert "9999" in note and "re-runs" in note


def test_a_matching_recorded_size_produces_no_note(tmp_path):
    client = _Client()
    scan, image = _scan(1, "P1"), _image(1, size=len(b"bytes::gravi/1.jpg"))
    result = pd.download_images(client, [scan], {1: image}, tmp_path)
    assert result.frames[0].note == ""


def test_the_size_note_reaches_the_download_log(tmp_path):
    import bloomctl._download as shared

    client = _Client()
    result = pd.download_images(client, [_scan(1, "P1")], {1: _image(1, size=9999)}, tmp_path)
    log = tmp_path / "log.txt"
    shared.write_download_log(result, log, noun="capture")
    assert "note=" in log.read_text()


def test_an_empty_file_is_not_treated_as_complete(tmp_path):
    client = _Client()
    scan, image = _scan(1, "P1"), _image(1, size=None)
    dest = pd.image_dest(tmp_path, scan, image)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"")

    assert pd.download_images(client, [scan], {1: image}, tmp_path).downloaded == 1


def test_orphan_temp_files_are_swept_before_the_run(tmp_path):
    orphan = tmp_path / "images" / "Wave3" / "P1" / ".dl-deadbeef.tmp"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"partial")
    os.utime(orphan, (0, 0))  # left by a run that is long gone, not one still writing

    pd.download_images(_Client(), [_scan(1, "P1")], {1: _image(1)}, tmp_path)

    assert not orphan.exists()


def test_a_temp_file_from_a_live_run_is_left_alone(tmp_path):
    """A temp cannot be told from a live one by name, so a fresh one is another run's."""
    live = tmp_path / "images" / "Wave3" / "P1" / ".dl-inflight.tmp"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"being written right now")

    pd.download_images(_Client(), [_scan(1, "P1")], {1: _image(1)}, tmp_path)

    assert live.exists(), "sweeping a live temp breaks the run that is writing it"


# --------------------------------------------------------------------------- #
# Collisions
# --------------------------------------------------------------------------- #


def test_two_scans_mapping_to_one_file_are_refused(tmp_path):
    # Same plate, same wave, same capture instant and cycle: one destination, two scans.
    a = _scan(1, "P1")
    b = _scan(2, "P1")
    with pytest.raises(pd.CollidingFrames) as excinfo:
        pd.download_images(_Client(), [a, b], {1: _image(1), 2: _image(2)}, tmp_path)
    assert "scan 1" in str(excinfo.value) and "scan 2" in str(excinfo.value)


def test_nothing_is_downloaded_when_a_collision_is_found(tmp_path):
    client = _Client()
    with pytest.raises(pd.CollidingFrames):
        pd.download_images(
            client, [_scan(1, "P1"), _scan(2, "P1")], {1: _image(1), 2: _image(2)}, tmp_path
        )
    assert client.bucket.requested == []


def test_plate_ids_differing_only_by_case(tmp_path):
    import bloomctl._download as shared

    scans = [_scan(1, "st0-001"), _scan(2, "ST0-001")]
    images = {1: _image(1), 2: _image(2)}
    if shared.filesystem_folds_case(tmp_path):
        with pytest.raises(pd.CollidingFrames):
            pd.download_images(_Client(), scans, images, tmp_path)
    else:
        assert pd.download_images(_Client(), scans, images, tmp_path).ok == 2


def test_different_cycles_of_one_plate_do_not_collide(tmp_path):
    scans = [
        _scan(1, "P1", cycle_number=0, capture_date="2026-05-27T14:03:11+00:00"),
        _scan(2, "P1", cycle_number=1, capture_date="2026-05-27T14:13:11+00:00"),
    ]
    result = pd.download_images(_Client(), scans, {1: _image(1), 2: _image(2)}, tmp_path)
    assert result.ok == 2


# --------------------------------------------------------------------------- #
# Disk full
# --------------------------------------------------------------------------- #


def test_disk_full_abandons_work_that_has_not_started(tmp_path, monkeypatch):
    import bloomctl._download as shared

    def _no_space(path, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shared, "atomic_write_bytes", _no_space)

    scans = [_scan(i, f"P{i}") for i in range(1, 21)]
    images = {i: _image(i) for i in range(1, 21)}
    result = pd.download_images(_Client(), scans, images, tmp_path, workers=1)

    assert result.failed == 20
    assert any("nowhere left to write" in f.error.lower() for f in result.frames)
    # Carried out of the run, or the log cannot say why it stopped.
    assert result.disk_full is True


def test_captures_already_on_disk_are_still_present_once_the_disk_fills(tmp_path, monkeypatch):
    """A resumed run that runs out of space must not report what it already has as missing.

    The stop event is checked after the resume check, not before: an object already on disk
    needs nothing written, so reporting it as failed would tell a scientist a whole
    experiment was lost when half of it is sitting in the directory.
    """
    import bloomctl._download as shared

    scans = [_scan(i, f"P{i}") for i in range(1, 5)]
    images = {i: _image(i) for i in range(1, 5)}

    # Scan 1 must be fetched and is what fills the disk. Scans 2-4 are already complete from
    # an earlier run, and are reached *after* the stop event is set — which is the only order
    # that tests anything: put them first and they are handled before the disk ever fills.
    for i in (2, 3, 4):
        dest = pd.image_dest(tmp_path, scans[i - 1], images[i])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x" * images[i]["file_size_bytes"])

    def _no_space(path, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shared, "atomic_write_bytes", _no_space)

    result = pd.download_images(_Client(), scans, images, tmp_path, workers=1)

    present = {f.scan_id for f in result.frames if f.ok}
    assert present == {2, 3, 4}, "captures already on disk were reported as failed"
    assert result.skipped == 3
    assert result.disk_full is True


def test_an_expired_session_is_named_rather_than_a_missing_bucket(tmp_path):
    class _Expired(_Bucket):
        def download(self, object_path):
            raise RuntimeError("{'statusCode': 404, 'message': Bucket not found}")

    client = _Client()
    client.bucket = _Expired()
    result = pd.download_images(client, [_scan(1, "P1")], {1: _image(1)}, tmp_path)
    assert "expired session" in result.frames[0].error


def test_stop_event_short_circuits_a_pending_scan(tmp_path):
    stop = threading.Event()
    stop.set()
    outcome = pd.download_plate_image(
        _Client(), _scan(1, "P1"), _image(1), tmp_path, stop=stop
    )
    assert outcome.ok is False and "nowhere left to write" in outcome.error.lower()
