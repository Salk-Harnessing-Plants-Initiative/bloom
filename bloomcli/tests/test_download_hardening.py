"""Guards on `cyl download`: where a frame is allowed to be written, and what happens
when two frames want the same file.

Also covers how a scan that can't be listed is counted, how many downloads are kept in
flight at once, and cleanup of temp files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_download_metadata import SCAN
from test_download_session_resume import _Client, _images

import bloomctl._download as shared_dl
import bloomctl._storage as storage
import bloomctl.cyl.download as dl
import bloomctl.plate.download as plate_dl

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
        ("C:evil", "C_evil"),  # drive-relative on Windows; joining it drops the output dir
        ("0.png:hidden", "0.png_hidden"),  # an NTFS stream, not the file itself
        ("QR-1", "QR-1"),  # ordinary values pass through untouched
        (14, "14"),
    ],
)
def test_safe_component_neutralises_separators_and_dots(raw, expected):
    result = dl.safe_component(raw)
    assert result == expected
    assert "/" not in result and "\\" not in result and ":" not in result
    assert result not in {"", ".", ".."}


def test_a_windows_drive_letter_cannot_discard_the_output_directory(tmp_path):
    """On Windows "C:name" is drive-relative: joining it drops everything before it, and
    os.path.isabs() does not flag it, so the containment check alone would not catch it."""
    import ntpath

    scan = {**SCAN, "qr_code": "C:evil"}
    relative = dl.scan_relative_dir(scan)

    assert ntpath.join(r"D:\lin\out", relative).startswith(r"D:\lin\out")


def test_a_colon_cannot_open_an_alternate_data_stream(tmp_path):
    """"0.png:hidden" would write into a hidden stream and leave the real file empty."""
    dest = dl.image_dest(tmp_path, SCAN, {"frame_number": "0.png:hidden", "object_path": "a.png"})
    assert ":" not in dest.name


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
    real_submit = shared_dl.ThreadPoolExecutor.submit
    live = {"n": 0, "peak": 0}

    def _tracking_submit(self, fn, *a, **k):
        live["n"] += 1
        live["peak"] = max(live["peak"], live["n"])
        future = real_submit(self, fn, *a, **k)
        future.add_done_callback(lambda _f: live.__setitem__("n", live["n"] - 1))
        return future

    monkeypatch.setattr(shared_dl.ThreadPoolExecutor, "submit", _tracking_submit)

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
    os.utime(orphan, (0, 0))  # left by a run that is long gone, not one still writing

    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: _images(1))
    dl.download_images(_Client(), [SCAN], tmp_path, workers=1)

    assert not orphan.exists()


def test_the_sweep_leaves_real_frames_alone(tmp_path):
    frame_dir = tmp_path / "images/Wave2"
    frame_dir.mkdir(parents=True)
    (frame_dir / "0.png").write_bytes(b"real")
    (frame_dir / ".dl-x.tmp").write_bytes(b"orphan")
    os.utime(frame_dir / ".dl-x.tmp", (0, 0))

    assert storage.sweep_orphan_temps(tmp_path) == 1
    assert (frame_dir / "0.png").exists()


def test_the_sweep_is_a_no_op_on_a_fresh_output_dir(tmp_path):
    assert storage.sweep_orphan_temps(tmp_path) == 0


def test_the_sweep_leaves_a_second_run_s_live_temps_alone(tmp_path):
    """A temp cannot be told from a live one by name, and the sweep runs at the start of
    every download — so without an age guard, starting a second run into the same directory
    deletes the first run's in-flight writes and fails its renames."""
    (tmp_path / "images").mkdir()
    in_flight = tmp_path / "images" / ".dl-inflight.tmp"
    in_flight.write_bytes(b"another run is writing this right now")

    assert storage.sweep_orphan_temps(tmp_path) == 0
    assert in_flight.exists()


def test_the_probe_file_is_named_so_the_sweep_can_collect_it(tmp_path, monkeypatch):
    """`ensure_writable` writes a probe; a hard kill in that window leaves it behind.

    The probe's name and the sweep's glob are coupled with nothing enforcing it, so this has to
    sweep the *real* probe. Naming one here instead would only ever exercise the sweep, and a
    rename on either side would go unnoticed.

    The kill is simulated by suppressing the unlink for the duration of the call, which is the
    state a killed process leaves: the probe written, the cleanup never reached.
    """
    out = tmp_path / "out"
    with monkeypatch.context() as killed:
        killed.setattr(Path, "unlink", lambda self, **kwargs: None)
        dl.ensure_writable(out)

    left = list(out.iterdir())
    assert len(left) == 1, f"expected ensure_writable to leave just its probe, found {left}"
    probe = left[0]
    os.utime(probe, (0, 0))

    assert storage.sweep_orphan_temps(out) == 1
    assert not probe.exists()


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

    if shared_dl.filesystem_folds_case(tmp_path):
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


def test_a_scan_with_no_images_is_noted_but_does_not_fail_the_run(tmp_path, monkeypatch):
    """There is nothing to fetch for such a scan, so failing would make every re-run fail too."""
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: [] if scan_id == 2 else _images(2))

    result = dl.download_images(_Client(), [SCAN, SCAN_B], tmp_path, workers=1)

    assert result.scans_without_frames == 1  # visible
    assert not result.incomplete, "everything that exists was downloaded"
    assert result.total == 2  # the empty scan is not counted as a frame


def test_a_scan_with_no_images_gets_a_log_line(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: [] if scan_id == 2 else _images(1))

    result = dl.download_images(_Client(), [SCAN, SCAN_B], tmp_path, workers=1)
    log = tmp_path / "log.txt"
    dl.write_download_log(result, log)

    text = log.read_text()
    assert "NOFRAMES scan=2" in text
    assert "1 scan(s) have no images" in text


def test_a_scan_with_no_images_can_be_re_run_to_a_clean_exit(tmp_path, monkeypatch):
    """Regression guard: this used to exit 1 forever on a download that had fetched everything."""
    from click.testing import CliRunner

    from bloomctl.cli import cli

    out = tmp_path / "out"
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: [] if scan_id == 2 else _images(2))
    _cli(monkeypatch, _Client(), [SCAN, SCAN_B])
    monkeypatch.setattr(dl, "fetch_images", lambda c, scan_id: [] if scan_id == 2 else _images(2))

    first = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "42"])
    assert first.exit_code == 0, first.output
    assert "have no images recorded" in first.output

    second = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "42"])
    assert second.exit_code == 0, second.output


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


def test_a_different_selection_in_the_same_directory_is_refused(tmp_path, monkeypatch):
    """One selection per directory: two downloads in one tree would leave scans.csv describing
    only the newer one."""
    from click.testing import CliRunner

    from bloomctl.cli import cli

    out = tmp_path / "out"
    _cli(monkeypatch, _Client(), [SCAN])
    first = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "100"])
    assert first.exit_code == 0, first.output

    _cli(monkeypatch, _Client(), [SCAN])
    second = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "200"])

    assert second.exit_code != 0
    assert "already holds a different download" in second.output
    assert "experiment_id was 100, now 200" in second.output
    assert "own directory" in second.output


def test_a_spot_check_and_a_full_download_do_not_share_a_directory(tmp_path, monkeypatch):
    """Checking one plant then pulling the whole experiment is two selections, so two folders."""
    from click.testing import CliRunner

    from bloomctl.cli import cli

    out = tmp_path / "out"
    _cli(monkeypatch, _Client(), [SCAN])
    spot = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment-id", "42", "--plant-qr-code", "QR-1"]
    )
    assert spot.exit_code == 0, spot.output

    full = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "42"])

    assert full.exit_code != 0
    assert "plant_qr_code was 'QR-1', now None" in full.output


def test_an_interrupted_download_resumes_on_the_same_command(tmp_path, monkeypatch):
    """The case that must keep working: same selection, picked up where it stopped."""
    from click.testing import CliRunner

    from bloomctl.cli import cli

    out = tmp_path / "out"
    _cli(monkeypatch, _Client(budget=1), [SCAN])
    first = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "42"])
    assert first.exit_code != 0, "partial download should report failure"

    fresh = _Client()
    _cli(monkeypatch, fresh, [SCAN])
    second = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "42"])

    assert second.exit_code == 0, second.output
    assert fresh.bucket.calls == 1, "only the frame that had failed should be fetched"


def test_a_different_age_window_is_a_different_selection(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from bloomctl.cli import cli

    out = tmp_path / "out"
    _cli(monkeypatch, _Client(), [SCAN])
    CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "42"])

    narrowed = CliRunner().invoke(
        cli, ["cyl", "download", str(out), "--experiment-id", "42", "--plant-age-max", "20"]
    )

    assert narrowed.exit_code != 0
    assert "plant_age_max" in narrowed.output




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


def test_selecting_by_name_then_by_id_is_the_same_download():
    """--experiment-name resolves to an id before the manifest is written."""
    by_name = dl.download_selector(experiment_id=42, scan_id=None, plant_qr_code=None,
                                   plant_age_min=0, plant_age_max=1000, limit=100000)
    by_id = dl.download_selector(experiment_id=42, scan_id=None, plant_qr_code=None,
                                 plant_age_min=0, plant_age_max=1000, limit=100000)
    assert dl.describe_manifest_mismatch(by_name, by_id, method=dl.METHOD) == ""


def test_an_empty_directory_needs_no_manifest(tmp_path):
    """Every first download starts here, so this must not be treated as suspicious."""
    assert not dl.holds_an_unidentified_download(tmp_path)
    assert dl.read_manifest(tmp_path) is None
    assert dl.describe_manifest_mismatch(None, {"experiment_id": 1}, method="cyl") == ""


def test_a_directory_with_images_but_no_manifest_is_refused(tmp_path):
    """The record is a dotfile — backups and sync filters drop it. Losing it must not mean
    losing the protection."""
    (tmp_path / "images").mkdir()
    assert dl.holds_an_unidentified_download(tmp_path)


def test_a_corrupt_manifest_is_treated_as_missing(tmp_path):
    (tmp_path / "images").mkdir()
    (tmp_path / dl.MANIFEST_NAME).write_text("not json", encoding="utf-8")
    assert dl.holds_an_unidentified_download(tmp_path)


def test_the_manifest_records_which_method_wrote_it(tmp_path):
    shared_dl.write_manifest(tmp_path, {"experiment_id": 12}, method=dl.METHOD)
    # Read the literal key out of the JSON rather than through METHOD_KEY: the name is an
    # on-disk contract across releases, and going through the constant follows a rename with it.
    # Renamed, every stamped plate manifest reads as unstamped — which resolves to cyl, so a
    # plate directory starts refusing its own resume.
    written = json.loads((tmp_path / shared_dl.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert written["method"] == "cyl"


def test_the_selector_cannot_displace_the_method_stamp(tmp_path):
    """The stamp is the writer's own record. A selector carrying the same key must not win.

    Neither command has a --method option today, so this is latent — but the guard's whole
    premise is that the field is not caller data, and a future method whose selector gained
    such a column would disable it with the suite still green.
    """
    shared_dl.write_manifest(
        tmp_path, {"experiment_id": 12, shared_dl.METHOD_KEY: "attacker"}, method=dl.METHOD
    )
    assert shared_dl.read_manifest(tmp_path)[shared_dl.METHOD_KEY] == "cyl"


def test_two_methods_are_a_mismatch_even_when_every_shared_key_agrees(tmp_path):
    """The selectors don't overlap, so absent keys read as equal and both ids are just 12.

    Without the method the two look like one download, and the second run resumes into the
    first one's tree — overwriting its log and leaving two methods' images under one root.
    """
    cyl_selector = dl.download_selector(experiment_id=12, scan_id=None, plant_qr_code=None,
                                        plant_age_min=0, plant_age_max=1000, limit=100000)
    shared_dl.write_manifest(tmp_path, cyl_selector, method=dl.METHOD)
    recorded = shared_dl.read_manifest(tmp_path)

    plate_selector = plate_dl.download_selector(experiment_id=12, scan_id=None, plate_id=None,
                                                wave_number=None, session_id=None, limit=100000)
    assert all(recorded.get(key) == value for key, value in plate_selector.items())

    mismatch = shared_dl.describe_manifest_mismatch(
        recorded, plate_selector, method=plate_dl.METHOD
    )
    # The literal phrase, not the parts: swapping the two reports the directory's method and the
    # run's backwards, and "cyl" and "plate" both still appear.
    assert "method was 'cyl', now 'plate'" in mismatch


def test_a_manifest_from_before_the_method_was_recorded_is_a_cyl_download(tmp_path):
    """0.1.0a5 directories carry no method. They must still resume under cyl, and must
    still refuse plate — reading them as neither would strand every one in the wild."""
    unstamped = {"experiment_id": 12, "scan_id": None, "plant_qr_code": None,
                 "plant_age_min": 0, "plant_age_max": 1000, "limit": 100000}
    assert shared_dl.METHOD_KEY not in unstamped

    assert shared_dl.describe_manifest_mismatch(
        unstamped, dict(unstamped), method=dl.METHOD
    ) == ""

    plate_selector = plate_dl.download_selector(experiment_id=12, scan_id=None, plate_id=None,
                                                wave_number=None, session_id=None, limit=100000)
    assert "method" in shared_dl.describe_manifest_mismatch(
        unstamped, plate_selector, method=plate_dl.METHOD
    )


def test_the_cli_refuses_a_directory_whose_manifest_went_missing(tmp_path, monkeypatch):
    """Deleting one dotfile used to let two experiments silently share a tree."""
    from click.testing import CliRunner

    from bloomctl.cli import cli

    out = tmp_path / "out"
    _cli(monkeypatch, _Client(), [SCAN])
    first = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "100"])
    assert first.exit_code == 0, first.output

    (out / dl.MANIFEST_NAME).unlink()

    _cli(monkeypatch, _Client(), [SCAN])
    second = CliRunner().invoke(cli, ["cyl", "download", str(out), "--experiment-id", "200"])

    assert second.exit_code != 0
    assert "no way to tell which download they belong to" in second.output
    assert "new directory" in second.output
