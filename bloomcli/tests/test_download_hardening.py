"""bloom#623 review — guards added after the 5-agent review of the #525/#534 work.

Path traversal, colliding destinations, unlisted-scan accounting, bounded submission,
and temp-file hygiene. Each test corresponds to a finding that was reproduced against
the pre-hardening code.
"""

from __future__ import annotations

import pytest
from test_download_metadata import SCAN
from test_download_session_resume import _Client, _images

import bloomctl.cyl._storage as storage
import bloomctl.cyl.download as dl

SCAN_B = {**SCAN, "scan_id": 2, "qr_code": "QR-2"}


# --- path traversal ---------------------------------------------------------


def test_a_traversing_qr_code_cannot_escape_the_output_dir(tmp_path):
    scan = {**SCAN, "qr_code": "../../../../escaped"}
    dest = dl.image_dest(tmp_path / "out", scan, {"frame_number": 0, "object_path": "a.png"})

    assert dest.is_relative_to((tmp_path / "out").resolve())
    assert "escaped" in dest.name or "escaped" in str(dest)


def test_an_absolute_frame_number_cannot_reset_the_path_root(tmp_path):
    """`Path / "/abs"` discards the left side entirely — that must not reach a write."""
    dest = dl.image_dest(
        tmp_path / "out", SCAN, {"frame_number": "/tmp/ABSOLUTE", "object_path": "a.png"}
    )

    assert dest.is_relative_to((tmp_path / "out").resolve())
    assert not str(dest).startswith("/tmp/ABSOLUTE")


def test_a_traversing_date_cannot_escape(tmp_path):
    scan = {**SCAN, "date_scanned": "../../.."}
    dest = dl.image_dest(tmp_path / "out", scan, {"frame_number": 0, "object_path": "a.png"})
    assert dest.is_relative_to((tmp_path / "out").resolve())


def test_a_traversal_never_reaches_disk(tmp_path, monkeypatch):
    """End to end: the escaped location must not be created."""
    scan = {**SCAN, "qr_code": "../../../../pwned"}
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(1))

    out = tmp_path / "out"
    dl.download_images(_Client(), [scan], out, workers=1)

    assert not (tmp_path.parent / "pwned").exists()
    assert list(out.rglob("*.png"))  # the frame still landed, just safely


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../..", ".._.."),  # separators gone, so it can no longer traverse
        ("a/b", "a_b"),
        ("a\\b", "a_b"),  # Windows-style separator too
        ("", "_"),
        (".", "_"),
        ("..", "_"),
        ("QR-1", "QR-1"),  # ordinary values pass through untouched
        (14, "14"),
    ],
)
def test_safe_component_neutralises_separators_and_dots(raw, expected):
    result = dl.safe_component(raw)
    assert result == expected
    assert "/" not in result and "\\" not in result
    assert result not in {"", ".", ".."}


# --- colliding destinations -------------------------------------------------


def test_two_null_frame_numbers_are_refused_rather_than_silently_merged(tmp_path, monkeypatch):
    """The review's reproduction: both rows mapped to None.png and one was never fetched."""
    images = [
        {"frame_number": None, "object_path": "cyl-images/a.png"},
        {"frame_number": None, "object_path": "cyl-images/b.png"},
    ]
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: images)

    with pytest.raises(ValueError, match="refusing to download"):
        dl.download_images(_Client(), [SCAN], tmp_path, workers=1)


def test_two_scans_mapping_to_one_directory_are_refused(tmp_path, monkeypatch):
    """Same wave/day/date/qr on two scans would interleave their frames."""
    twin = {**SCAN, "scan_id": 99}  # identical path components, different scan_id
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(2))

    with pytest.raises(ValueError, match="refusing to download"):
        dl.download_images(_Client(), [SCAN, twin], tmp_path, workers=1)


def test_distinct_scans_are_not_falsely_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(2))
    result = dl.download_images(_Client(), [SCAN, SCAN_B], tmp_path, workers=1)
    assert result.ok == 4 and result.failed == 0


def test_the_cli_reports_a_collision_as_a_clean_error(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from test_download_session_resume import CREDS

    import bloomctl.auth as auth
    from bloomctl.cli import cli

    monkeypatch.setattr("bloomctl.credentials.load_credentials", lambda *a, **k: CREDS)
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _Client())
    monkeypatch.setattr(dl, "fetch_scans", lambda *a, **k: [SCAN, {**SCAN, "scan_id": 99}])
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {})
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(1))

    result = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-id", "1"]
    )

    assert result.exit_code != 0
    assert "refusing to download" in result.output
    assert "Traceback" not in result.output  # a clean message, not a crash


# --- unlisted scans in the log ----------------------------------------------


def test_an_unlisted_scan_stays_in_scan_order_in_the_log(tmp_path, monkeypatch):
    """It used to be appended after every other scan's frames, orphaned at the bottom."""

    def _fetch_images(client, scan_id):
        if scan_id == 1:
            raise RuntimeError("PostgREST 500")
        return _images(2)

    monkeypatch.setattr(dl, "fetch_images", _fetch_images)

    result = dl.download_images(_Client(), [SCAN, SCAN_B], tmp_path, workers=1)
    log = tmp_path / "log.txt"
    dl.write_download_log(result, log)

    lines = [ln for ln in log.read_text().splitlines() if ln.strip()]
    assert lines[0].startswith("UNLISTED scan=1")  # first, where scan 1 belongs
    assert lines[1].startswith("OK   scan=2")


def test_an_unlisted_scan_is_not_counted_as_one_failed_frame(tmp_path, monkeypatch):
    def _fetch_images(client, scan_id):
        if scan_id == 1:
            raise RuntimeError("PostgREST 500")
        return _images(3)

    monkeypatch.setattr(dl, "fetch_images", _fetch_images)

    result = dl.download_images(_Client(), [SCAN, SCAN_B], tmp_path, workers=1)

    assert result.total == 3  # only the frames actually enumerated
    assert result.failed == 0 and result.scans_unlisted == 1
    assert result.incomplete  # ...but the dataset is still not complete


# --- bounded submission -----------------------------------------------------


def test_futures_in_flight_stay_bounded(tmp_path, monkeypatch):
    """`pool.map` submitted all 414k items up front (780MB vs 176MB measured)."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(500))
    in_flight = []
    real_submit = dl.ThreadPoolExecutor.submit
    live = {"n": 0, "peak": 0}

    def _tracking_submit(self, fn, *a, **k):
        live["n"] += 1
        live["peak"] = max(live["peak"], live["n"])
        future = real_submit(self, fn, *a, **k)
        future.add_done_callback(lambda _f: live.__setitem__("n", live["n"] - 1))
        return future

    monkeypatch.setattr(dl.ThreadPoolExecutor, "submit", _tracking_submit)

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=4)

    assert result.ok == 500
    assert live["peak"] <= 4 * 4 + 4, f"peak in-flight futures was {live['peak']}"
    assert in_flight == []


def test_bounded_submission_preserves_order(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(100))

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=8)

    assert [f.frame_number for f in result.frames] == list(range(100))


# --- temp-file hygiene ------------------------------------------------------


def test_a_stray_temp_from_a_killed_run_is_swept(tmp_path, monkeypatch):
    """SIGKILL skips atomic_write_bytes' cleanup handler, so a run sweeps on start."""
    frame_dir = tmp_path / "images/Wave2/Day14_2026-05-11/QR-1"
    frame_dir.mkdir(parents=True)
    orphan = frame_dir / ".dl-abc123.tmp"
    orphan.write_bytes(b"half a frame")

    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(1))
    dl.download_images(_Client(), [SCAN], tmp_path, workers=1)

    assert not orphan.exists()


def test_the_sweep_leaves_real_frames_alone(tmp_path):
    frame_dir = tmp_path / "images/Wave2"
    frame_dir.mkdir(parents=True)
    (frame_dir / "0.png").write_bytes(b"real")
    (frame_dir / ".dl-x.tmp").write_bytes(b"orphan")

    assert storage.sweep_orphan_temps(tmp_path) == 1
    assert (frame_dir / "0.png").exists()


def test_the_sweep_is_a_no_op_on_a_fresh_output_dir(tmp_path):
    assert storage.sweep_orphan_temps(tmp_path) == 0


# --- malformed rows ---------------------------------------------------------


def test_a_row_missing_object_path_gets_a_readable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: [{"frame_number": 0}])

    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=1)

    assert result.failed == 1
    assert "malformed cyl_images row" in result.frames[0].error
    assert "object_path" in result.frames[0].error
