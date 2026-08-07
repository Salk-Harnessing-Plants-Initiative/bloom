"""Guards on `cyl download`: where a frame is allowed to be written, and what happens
when two frames want the same file.

Also covers how a scan that can't be listed is counted, how many downloads are kept in
flight at once, and cleanup of temp files.
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
    """Joining an absolute path discards the output directory, so it must be neutralised."""
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

    assert not (tmp_path / "pwned").exists()  # this is where an escape would actually land
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
    """Both rows map to the same filename, so one image would never be fetched."""
    images = [
        {"frame_number": None, "object_path": "cyl-images/a.png"},
        {"frame_number": None, "object_path": "cyl-images/b.png"},
    ]
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: images)

    with pytest.raises(dl.CollidingFrames, match="are the same file"):
        dl.download_images(_Client(), [SCAN], tmp_path, workers=1)


def test_two_scans_mapping_to_one_directory_are_refused(tmp_path, monkeypatch):
    """Two scans sharing wave/day/date/QR would write into the same directory."""
    twin = {**SCAN, "scan_id": 99}  # identical path components, different scan_id
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(2))

    with pytest.raises(dl.CollidingFrames, match="are the same file"):
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
    assert "Refusing to download" in result.output
    assert "are the same file" in result.output
    assert "Traceback" not in result.output  # a clean message, not a crash


# --- unlisted scans in the log ----------------------------------------------


def test_an_unlisted_scan_stays_in_scan_order_in_the_log(tmp_path, monkeypatch):
    """It belongs where the scan is, not collected at the end of the log."""

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
    """Queueing every frame at once would cost hundreds of MB before the first one arrives."""
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
    """A hard kill skips the normal cleanup, so each run tidies up before it starts."""
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


# --- symlinked output directories -------------------------------------------


def test_a_symlinked_images_directory_still_works(tmp_path, monkeypatch):
    """Pointing images/ at another disk is an ordinary way to stage a big experiment."""
    out = tmp_path / "out"
    out.mkdir()
    bigdisk = tmp_path / "bigdisk"
    bigdisk.mkdir()
    (out / "images").symlink_to(bigdisk, target_is_directory=True)

    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(3))
    result = dl.download_images(_Client(), [SCAN], out, workers=1)

    assert result.ok == 3 and result.failed == 0
    assert (bigdisk / "Wave2/Day14_2026-05-11/QR-1/0.png").exists()


def test_containment_still_refuses_if_the_sanitiser_ever_regresses(tmp_path, monkeypatch):
    """`safe_component` is what makes traversal impossible; this is the backstop behind it."""
    monkeypatch.setattr(dl, "safe_component", lambda value: str(value))

    with pytest.raises(ValueError, match="refusing to write outside"):
        dl.image_dest(tmp_path, {**SCAN, "qr_code": "../../.."}, {"frame_number": "../../escape",
                                                                  "object_path": "a.png"})


# --- collisions the filesystem creates --------------------------------------


def test_two_qr_codes_differing_only_by_case_are_caught(tmp_path):
    """Postgres treats these as two plants; macOS treats their directories as one."""
    lower = {**SCAN, "scan_id": 1, "qr_code": "st0-001"}
    upper = {**SCAN, "scan_id": 2, "qr_code": "ST0-001"}
    work = [(lower, i) for i in _images(2)] + [(upper, i) for i in _images(2)]

    clashes = dl.find_frame_collisions(tmp_path, work)

    if dl.filesystem_folds_case(tmp_path):
        assert clashes, "these land on one file here, so they must be reported"
    else:
        assert not clashes, "these are genuinely different files here, so must not be"


def test_frames_differing_only_by_extension_do_not_collide(tmp_path):
    """0.png and 0.tif are different files; refusing them would be a false alarm."""
    pair = [
        (SCAN, {"frame_number": 0, "object_path": "a.png"}),
        (SCAN, {"frame_number": 0, "object_path": "a.tif"}),
    ]
    assert dl.find_frame_collisions(tmp_path, pair) == []


def test_a_trailing_space_makes_a_distinct_plant_not_a_collision(tmp_path):
    """qr_code is UNIQUE per wave, so "QR-1" and "QR-1 " are two plants, not one."""
    plain = {**SCAN, "scan_id": 1, "qr_code": "QR-1"}
    spaced = {**SCAN, "scan_id": 2, "qr_code": "QR-1 "}

    assert dl.scan_relative_dir(plain) != dl.scan_relative_dir(spaced)
    assert dl.find_frame_collisions(
        tmp_path, [(plain, {"frame_number": 0, "object_path": "a.png"}),
                   (spaced, {"frame_number": 0, "object_path": "a.png"})]
    ) == []


def test_all_collisions_are_reported_not_just_the_first(tmp_path):
    a = {**SCAN, "scan_id": 1}
    b = {**SCAN, "scan_id": 2}
    pairs = [(a, {"frame_number": n, "object_path": "x.png"}) for n in (0, 1)]
    pairs += [(b, {"frame_number": n, "object_path": "x.png"}) for n in (0, 1)]

    assert len(dl.find_frame_collisions(tmp_path, pairs)) == 2


# --- scans with no images ---------------------------------------------------


def test_a_scan_with_no_images_is_not_silently_successful(tmp_path, monkeypatch):
    """An interrupted upload leaves a scan row with no cyl_images — that is missing data."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: [] if scan_id == 2 else _images(2))

    result = dl.download_images(_Client(), [SCAN, SCAN_B], tmp_path, workers=1)

    assert result.scans_without_frames == 1
    assert result.incomplete, "a scan with no images must not exit 0"
    assert result.total == 2  # the empty scan is not counted as a frame


def test_a_scan_with_no_images_gets_a_log_line(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: [] if scan_id == 2 else _images(1))

    result = dl.download_images(_Client(), [SCAN, SCAN_B], tmp_path, workers=1)
    log = tmp_path / "log.txt"
    dl.write_download_log(result, log)

    text = log.read_text()
    assert "NOFRAMES scan=2" in text
    assert "1 scan(s) have no images" in text


def test_an_experiment_with_no_images_at_all_exits_non_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: [])
    result = dl.download_images(_Client(), [SCAN], tmp_path, workers=1)
    assert result.total == 0 and result.incomplete


# --- log integrity ----------------------------------------------------------


def test_a_multi_line_error_stays_on_one_log_line(tmp_path):
    """A 502 from httpx has a multi-line message; it must not look like extra frames."""
    frames = [
        dl.FrameResult(3, 0, "a.png", ok=True),
        dl.FrameResult(3, 1, "b.png", ok=False, error="502 Bad Gateway\nFor more information: x"),
        dl.FrameResult(3, 2, "c.png", ok=True),
    ]
    log = tmp_path / "log.txt"
    dl.write_download_log(dl.DownloadResult(frames), log)

    records = [ln for ln in log.read_text().splitlines() if ln.strip()]
    assert len(records) == 4  # 3 frames + summary, not 5
    assert "For more information: x" in records[1]


def test_a_newline_in_an_object_path_cannot_forge_a_log_record(tmp_path):
    frames = [dl.FrameResult(3, 0, "a.png\nOK   scan=999 frame=1 forged.png", ok=True)]
    log = tmp_path / "log.txt"
    dl.write_download_log(dl.DownloadResult(frames), log)

    records = [ln for ln in log.read_text().splitlines() if ln.strip()]
    assert len(records) == 2  # 1 frame + summary
    assert "scan=999" in records[0]  # present, but folded into the real record


# --- one output directory, one download -------------------------------------


def _cli(monkeypatch, client, scans, images=2):
    from test_download_session_resume import CREDS

    import bloomctl.auth as auth

    monkeypatch.setattr("bloomctl.credentials.load_credentials", lambda *a, **k: CREDS)
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: client)
    monkeypatch.setattr(dl, "fetch_scans", lambda *a, **k: scans)
    monkeypatch.setattr(dl, "fetch_genotypes", lambda c, ids: {})
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(images))


def test_a_second_experiment_into_the_same_directory_is_refused(tmp_path, monkeypatch):
    """Paths carry no experiment id, so resume could serve the first one's images as the second's."""
    from click.testing import CliRunner

    from bloomctl.cli import cli

    out = tmp_path / "out"
    _cli(monkeypatch, _Client(), [{**SCAN, "experiment_id": 100}])
    first = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "100"])
    assert first.exit_code == 0, first.output

    _cli(monkeypatch, _Client(), [{**SCAN, "experiment_id": 200}])
    second = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "200"])

    assert second.exit_code != 0
    assert "holds experiment [100]" in second.output
    assert "this download is for experiment [200]" in second.output


def test_overwrite_does_not_let_two_experiments_share_a_directory(tmp_path, monkeypatch):
    """--overwrite re-fetches this download's frames; it does not remove the other's."""
    from click.testing import CliRunner

    from bloomctl.cli import cli

    out = tmp_path / "out"
    _cli(monkeypatch, _Client(), [{**SCAN, "experiment_id": 100}])
    CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "100"])

    _cli(monkeypatch, _Client(), [{**SCAN, "experiment_id": 200}])
    result = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment-id", "200", "--overwrite"]
    )

    assert result.exit_code != 0, "--overwrite must not be an escape hatch for mixing experiments"
    assert dl.read_manifest(out)["experiment_ids"] == [100]


def test_overwrite_re_fetches_frames_that_are_already_on_disk(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from bloomctl.cli import cli

    out = tmp_path / "out"
    scans = [{**SCAN, "experiment_id": 42}]
    _cli(monkeypatch, _Client(), scans)
    CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "42"])

    fresh = _Client()
    _cli(monkeypatch, fresh, scans)
    again = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment-id", "42", "--overwrite"]
    )

    assert again.exit_code == 0, again.output
    assert fresh.bucket.calls == 2, "--overwrite must re-fetch, not resume"


def test_narrowing_then_widening_the_same_experiment_resumes(tmp_path, monkeypatch):
    """Checking one plant and then pulling the whole experiment is a normal way to work."""
    from click.testing import CliRunner

    from bloomctl.cli import cli

    out = tmp_path / "out"
    one = {**SCAN, "experiment_id": 42}
    _cli(monkeypatch, _Client(), [one])
    first = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment-id", "42", "--plant-qr-code", "QR-1"]
    )
    assert first.exit_code == 0, first.output

    fresh = _Client()
    _cli(monkeypatch, fresh, [one, {**SCAN, "scan_id": 2, "qr_code": "QR-2", "experiment_id": 42}])
    wider = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "42"])

    assert wider.exit_code == 0, wider.output
    assert fresh.bucket.calls == 2, "only the newly-included plant should be fetched"


def test_a_run_that_matches_no_scans_fails(tmp_path, monkeypatch):
    """An empty dataset must not look like a successful download to a pipeline."""
    from click.testing import CliRunner

    from bloomctl.cli import cli

    _cli(monkeypatch, _Client(), [])
    result = CliRunner().invoke(
        cli, ["cyl", "download", str(tmp_path / "out"), "--experiment-id", "42"]
    )

    assert result.exit_code != 0
    assert "No scans matched" in result.output


def test_identity_comes_from_the_rows_not_the_flags():
    scans = [{"experiment_id": 7}, {"experiment_id": 7}, {"experiment_id": None}]
    assert dl.download_identity(scans) == {"experiment_ids": [7]}
    assert dl.describe_manifest_mismatch({"experiment_ids": [7]}, {"experiment_ids": [7]}) == ""
    assert dl.describe_manifest_mismatch({"experiment_ids": [7]}, {"experiment_ids": [8]})


def test_a_missing_or_corrupt_manifest_does_not_block_a_download(tmp_path):
    assert dl.read_manifest(tmp_path) is None
    (tmp_path / dl.MANIFEST_NAME).write_text("not json", encoding="utf-8")
    assert dl.read_manifest(tmp_path) is None
    assert dl.describe_manifest_mismatch(None, {"experiment_ids": [1]}) == ""
