"""`bloomctl plate download` — the command surface: selectors, name resolution, guards, exits."""

from __future__ import annotations

from click.testing import CliRunner
from test_plate_download_paths import IMAGE, SCAN

import bloomctl._download as shared_dl
import bloomctl.auth as auth
import bloomctl.plate.download as pd
from bloomctl.cli import cli
from bloomctl.credentials import Credentials

CREDS = Credentials("https://x/api", "KEY", "u@s.edu", "pw")


def _signed_in(monkeypatch, client=None):
    monkeypatch.setattr("bloomctl.credentials.load_credentials", lambda *a, **k: CREDS)
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: client or object())


def _one_scan(monkeypatch, scans=None, images=None):
    monkeypatch.setattr(pd, "fetch_plate_scans", lambda *a, **k: scans or [SCAN])
    monkeypatch.setattr(pd, "fetch_plate_images", lambda c, ids: images or {1: IMAGE})
    monkeypatch.setattr(pd, "fetch_plate_sections", lambda c, ids: [])


def _run(*args):
    return CliRunner().invoke(cli, ["plate", "download", *args])


# --------------------------------------------------------------------------- #
# Group registration
# --------------------------------------------------------------------------- #


def test_plate_group_is_registered():
    result = CliRunner().invoke(cli, ["--help"])
    assert "plate" in result.output


def test_plate_group_lists_download():
    result = CliRunner().invoke(cli, ["plate", "--help"])
    assert result.exit_code == 0
    assert "download" in result.output


# --------------------------------------------------------------------------- #
# Selector validation — before any network call
# --------------------------------------------------------------------------- #


def test_no_primary_selector_is_a_usage_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        auth, "make_authed_client", lambda c: (_ for _ in ()).throw(AssertionError("no network"))
    )
    result = _run(str(tmp_path / "out"))
    assert result.exit_code != 0
    assert "exactly one" in result.output.lower()


def test_two_primary_selectors_are_a_usage_error(tmp_path):
    result = _run(str(tmp_path / "out"), "--experiment-id", "12", "--scan-id", "1")
    assert result.exit_code != 0
    assert "exactly one" in result.output.lower()


def test_three_primary_selectors_are_a_usage_error(tmp_path):
    result = _run(
        str(tmp_path / "out"), "--experiment-id", "12", "--scan-id", "1", "--experiment-name", "x"
    )
    assert result.exit_code != 0


def test_species_without_a_name_is_a_usage_error(tmp_path):
    result = _run(str(tmp_path / "out"), "--experiment-id", "12", "--species", "Pennycress")
    assert result.exit_code != 0
    assert "--species" in result.output


def test_workers_above_the_maximum_is_rejected(tmp_path):
    result = _run(str(tmp_path / "out"), "--experiment-id", "12", "--workers", "999")
    assert result.exit_code != 0


def test_workers_below_one_is_rejected(tmp_path):
    result = _run(str(tmp_path / "out"), "--experiment-id", "12", "--workers", "0")
    assert result.exit_code != 0


def test_the_workers_flag_reaches_the_download(tmp_path, monkeypatch):
    # Validating the flag's range proves nothing about it being used. Without this, dropping
    # `workers=workers` at the call site would leave every download stuck on the default.
    seen = {}
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(
        pd,
        "download_images",
        lambda *a, **k: seen.update(k) or pd.DownloadResult([]),
    )

    _run(str(tmp_path / "out"), "--experiment-id", "12", "--workers", "3")

    assert seen["workers"] == 3


def test_the_default_worker_count_is_concurrent(tmp_path, monkeypatch):
    seen = {}
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(
        pd,
        "download_images",
        lambda *a, **k: seen.update(k) or pd.DownloadResult([]),
    )

    _run(str(tmp_path / "out"), "--experiment-id", "12")

    assert seen["workers"] == pd.DEFAULT_WORKERS > 1


def test_missing_credentials_hints_at_login(tmp_path, monkeypatch):
    def _no_creds(*a, **k):
        raise FileNotFoundError("no credentials file")

    monkeypatch.setattr("bloomctl.credentials.load_credentials", _no_creds)
    result = _run(str(tmp_path / "out"), "--experiment-id", "12")
    assert result.exit_code != 0
    assert "bloomctl login" in result.output


# --------------------------------------------------------------------------- #
# Experiment-name resolution
# --------------------------------------------------------------------------- #


def _match(id_, name, system=None):
    return {
        "id": id_,
        "name": name,
        "species_id": 3,
        "species_name": "Pennycress",
        "system_name": system,
        "created_at": "2026-05-01",
    }


def test_unique_name_resolves_and_reports(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(pd, "search_experiments", lambda *a, **k: [_match(12, "Gravi 2026-05")])
    monkeypatch.setattr(pd, "download_images", lambda *a, **k: pd.DownloadResult([]))

    result = _run(str(tmp_path / "out"), "--experiment-name", "gravi")

    assert result.exit_code == 0
    assert "Gravi 2026-05" in result.stderr and "12" in result.stderr


def test_ambiguous_name_lists_the_rig_and_downloads_nothing(tmp_path, monkeypatch):
    # The gravi-specific case: UNIQUE(species_id, name, system_name) makes one name on two
    # rigs legal, so the listing has to show system_name or the rows look identical.
    _signed_in(monkeypatch)
    monkeypatch.setattr(
        pd,
        "search_experiments",
        lambda *a, **k: [_match(12, "twin", "GRAV-01"), _match(13, "twin", "GRAV-02")],
    )

    def _must_not_download(*a, **k):
        raise AssertionError("must not download on an ambiguous match")

    monkeypatch.setattr(pd, "download_images", _must_not_download)

    result = _run(str(tmp_path / "out"), "--experiment-name", "twin")

    assert result.exit_code != 0
    assert "GRAV-01" in result.output and "GRAV-02" in result.output
    assert "--experiment-id" in result.output


def test_no_match_exits_non_zero(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    monkeypatch.setattr(pd, "search_experiments", lambda *a, **k: [])
    result = _run(str(tmp_path / "out"), "--experiment-name", "nothing")
    assert result.exit_code != 0
    assert "No experiment matches" in result.output


def test_no_match_names_the_species_when_narrowed(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    monkeypatch.setattr(pd, "search_experiments", lambda *a, **k: [])
    result = _run(
        str(tmp_path / "out"), "--experiment-name", "x", "--species", "Pennycress"
    )
    assert "Pennycress" in result.output


def test_a_server_error_surfaces_its_message(tmp_path, monkeypatch):
    from postgrest import APIError

    _signed_in(monkeypatch)

    def _boom(*a, **k):
        # P0001 is a RAISE EXCEPTION — a sentence written for the user, so it is passed
        # on as-is. Without the code this exercises the wrapping path instead.
        raise APIError({"message": "search query too long (max 200 characters)", "code": "P0001"})

    monkeypatch.setattr(pd, "search_experiments", _boom)
    result = _run(str(tmp_path / "out"), "--experiment-name", "x" * 201)
    assert result.exit_code != 0
    assert "too long" in result.output
    assert "Could not read" not in result.output, (
        "the server's own sentence must reach the user unprefixed — a read-failure "
        "framing sends them to check the network over their own input"
    )


# --------------------------------------------------------------------------- #
# Outputs
# --------------------------------------------------------------------------- #


def test_meta_only_writes_the_csv_and_fetches_no_image(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)

    def _no_images(*a, **k):
        raise AssertionError("images must not download under --meta-only")

    monkeypatch.setattr(pd, "download_images", _no_images)

    out = tmp_path / "out"
    result = _run(str(out), "--experiment-id", "12", "--meta-only")

    assert result.exit_code == 0
    assert (out / "plates.csv").exists()
    assert not (out / "images").exists()


def test_sections_csv_is_written_when_metadata_exists(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(
        pd,
        "fetch_plate_sections",
        lambda c, ids: [
            {"metadata_id": 55, "plate_section_id": "top", "medium": "MS", "plant_qr": "QR-1"}
        ],
    )
    monkeypatch.setattr(pd, "download_images", lambda *a, **k: pd.DownloadResult([]))

    out = tmp_path / "out"
    _run(str(out), "--experiment-id", "12", "--meta-only")

    assert (out / "plate_sections.csv").exists()


def test_no_sections_file_when_no_scan_has_metadata(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(pd, "download_images", lambda *a, **k: pd.DownloadResult([]))

    out = tmp_path / "out"
    result = _run(str(out), "--experiment-id", "12", "--meta-only")

    assert result.exit_code == 0, "an absent sections file is not an error"
    assert not (out / "plate_sections.csv").exists()


def test_a_server_error_on_the_scan_query_is_a_message_not_a_traceback(tmp_path, monkeypatch):
    """Permission denied, a gateway blip, an unapplied migration — all arrive as an APIError.

    Unhandled, the user gets a Python stack with the useful sentence buried in it.
    """
    from postgrest import APIError

    _signed_in(monkeypatch)

    def _boom(*a, **k):
        raise APIError({"message": "permission denied for view gravi_scans_extended"})

    monkeypatch.setattr(pd, "fetch_plate_scans", _boom)

    result = _run(str(tmp_path / "out"), "--experiment-id", "12")

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit), "the APIError escaped click unhandled"
    assert "permission denied" in result.output
    assert "the scans for this experiment" in result.output


def test_a_failed_scan_read_names_itself(tmp_path, monkeypatch):
    from postgrest import APIError

    _signed_in(monkeypatch)

    def _boom(*a, **k):
        raise APIError({"message": "permission denied for view gravi_scans_extended"})

    monkeypatch.setattr(pd, "fetch_plate_scan", _boom)

    result = _run(str(tmp_path / "out"), "--scan-id", "77")

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit), "the APIError escaped click unhandled"
    assert "this scan" in result.output
    assert "permission denied for view gravi_scans_extended" in result.output


def test_a_failed_image_row_read_names_itself(tmp_path, monkeypatch):
    from postgrest import APIError

    _signed_in(monkeypatch)
    _one_scan(monkeypatch)  # the scan query succeeds; the image rows are the next read

    def _boom(*a, **k):
        raise APIError({"message": "relation gravi_images does not exist"})

    monkeypatch.setattr(pd, "fetch_plate_images", _boom)

    result = _run(str(tmp_path / "out"), "--experiment-id", "12")

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit), "the APIError escaped click unhandled"
    assert "the image rows for these scans" in result.output
    assert "relation gravi_images does not exist" in result.output


def test_a_read_timeout_resolving_a_name_is_a_sentence(tmp_path, monkeypatch):
    """The old handler caught APIError only, so a timeout here was the one failure it missed."""
    import httpx

    _signed_in(monkeypatch)

    def _boom(*a, **k):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(pd, "search_experiments", _boom)

    result = _run(str(tmp_path / "out"), "--experiment-name", "Gravi 2026-05")

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit), "the ReadTimeout escaped click unhandled"
    assert "the experiment names" in result.output
    assert "check your connection" in result.output


def test_a_failed_section_query_says_the_csv_is_already_written(tmp_path, monkeypatch):
    """This query runs after plates.csv and the manifest are on disk.

    Without saying so, the user is left with a directory that looks like a valid partial
    download and no idea which half of it is real.
    """
    from postgrest import APIError

    _signed_in(monkeypatch)
    _one_scan(monkeypatch)  # everything before the section query succeeds

    def _boom(*a, **k):
        raise APIError({"message": "gateway timeout"})

    monkeypatch.setattr(pd, "fetch_plate_sections", _boom)
    out = tmp_path / "out"

    result = _run(str(out), "--experiment-id", "12", "--meta-only")

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "plates.csv is already written" in result.output
    assert (out / "plates.csv").exists(), "the CSV it mentions really is there"


def test_a_full_disk_names_the_cause_and_does_not_cite_a_missing_log(tmp_path, monkeypatch):
    """A full disk is exactly when the log write fails too.

    Saying "see download_log.txt" then sends the reader to a file that was never written, at
    the moment they most need it — and without naming the cause they run `df`, see free space
    on quota-limited lab storage, and go looking for the wrong thing.
    """
    import bloomctl._download as shared

    _signed_in(monkeypatch)
    _one_scan(monkeypatch)

    failed = shared.FrameResult(1, 0, "gravi/1.png", ok=False, error="No space left on device")
    monkeypatch.setattr(
        pd, "download_images", lambda *a, **k: shared.DownloadResult([failed], disk_full=True)
    )

    def _no_log(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pd, "write_download_log", _no_log)

    result = _run(str(tmp_path / "out"), "--experiment-id", "12")

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "disk filled up or the storage quota was spent" in result.output
    assert "download_log.txt" not in result.output.split("Could not write")[-1].split("\n")[1], (
        "pointed at a log that was never written"
    )


def test_a_failed_metadata_write_names_the_file_and_the_cause(tmp_path, monkeypatch):
    """Atomic writes keep the previous plates.csv intact; the user still has to be told why."""
    import bloomctl.plate.download as plate_dl

    _signed_in(monkeypatch)
    _one_scan(monkeypatch)

    def _no_space(rows, path):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(plate_dl, "write_plates_csv", _no_space)

    result = _run(str(tmp_path / "out"), "--experiment-id", "12", "--meta-only")

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "plates.csv" in result.output
    assert "space" in result.output.lower()


def test_a_capped_fetch_says_the_newest_captures_are_missing(tmp_path, monkeypatch):
    """Ordered by scan_id, a cap drops the most recent captures across every plate at once.

    That looks exactly like an experiment that stopped early, so silence is the one thing
    this must not do. It also cannot tell that case from an experiment holding exactly this
    many scans, so it must not assert captures are missing either.
    """
    _signed_in(monkeypatch)
    monkeypatch.setattr(pd, "fetch_plate_scans", lambda *a, **k: [SCAN, SCAN, SCAN])
    monkeypatch.setattr(pd, "fetch_plate_images", lambda c, ids: {1: IMAGE})
    monkeypatch.setattr(pd, "fetch_plate_sections", lambda c, ids: [])

    result = _run(str(tmp_path / "out"), "--experiment-id", "12", "--limit", "3", "--meta-only")

    assert "exactly --limit 3" in result.output
    assert "nothing is missing" in result.output, "stated as fact what it cannot distinguish"
    assert "needs its own directory" in result.output, "advised raising --limit in place"


def test_scan_id_never_warns_about_the_limit(tmp_path, monkeypatch):
    """--scan-id fetches one named row and applies no cap, so the warning is always false there.

    It fired whenever --limit happened to be 1, telling a scientist their complete download
    was missing its newest captures.
    """
    _signed_in(monkeypatch)
    monkeypatch.setattr(pd, "fetch_plate_scan", lambda c, sid: SCAN)
    monkeypatch.setattr(pd, "fetch_plate_images", lambda c, ids: {1: IMAGE})
    monkeypatch.setattr(pd, "fetch_plate_sections", lambda c, ids: [])

    result = _run(str(tmp_path / "out"), "--scan-id", "1", "--limit", "1", "--meta-only")

    assert result.exit_code == 0
    assert "--limit" not in result.output


def test_no_scans_is_an_error_without_a_limit_warning_above_it(tmp_path, monkeypatch):
    """`--limit 0` matched the cap at zero rows, so the warning printed above the error."""
    _signed_in(monkeypatch)
    monkeypatch.setattr(pd, "fetch_plate_scans", lambda *a, **k: [])

    result = _run(str(tmp_path / "out"), "--experiment-id", "12", "--limit", "0", "--meta-only")

    assert result.exit_code != 0
    assert "No scans matched" in result.output
    assert "exactly --limit" not in result.output


def test_a_run_that_dies_writing_the_csv_still_leaves_the_directory_claimed(tmp_path, monkeypatch):
    """The manifest is written first, so the directory is stamped before any other file exists.

    With the CSV first there was a window holding plates.csv and no manifest — and no images/,
    so `holds_an_unidentified_download` was False. A `cyl download` into that directory then
    succeeded, ending with plates.csv and scans.csv side by side under a cyl stamp.
    """
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(pd, "write_plates_csv", _dies)
    out = tmp_path / "out"

    assert _run(str(out), "--experiment-id", "12", "--meta-only").exit_code != 0

    assert (out / pd.MANIFEST_NAME).exists(), "died before claiming the directory"
    assert not (out / "plates.csv").exists()

    # The stamp is what a later cyl run reads, and it now refuses this directory.
    import bloomctl.cyl.download as cd

    mismatch = shared_dl.describe_manifest_mismatch(
        shared_dl.read_manifest(out), cd.download_selector(experiment_id=12), method=cd.METHOD
    )
    assert "method was 'plate', now 'cyl'" in mismatch


def _dies(*a, **k):
    raise OSError(5, "killed mid-write")


def test_the_output_path_is_checked_before_anything_else_runs(tmp_path, monkeypatch):
    """A mistyped or unmounted target must cost a second, not a whole metadata phase.

    Without the check, `mkdir(parents=True)` on the way to plates.csv builds the missing tree
    on the boot disk and fills it with an experiment meant for a drive that is not mounted.
    """
    reached = []
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: reached.append("signed in") or CREDS,
    )
    unmounted = tmp_path / "not-mounted" / "run3"

    result = _run(str(unmounted), "--experiment-id", "12")

    assert result.exit_code != 0
    assert not unmounted.exists(), "created the tree for a path that does not exist"
    assert reached == [], "signed in before checking the path was usable"


def test_empty_selection_names_the_filters(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    monkeypatch.setattr(pd, "fetch_plate_scans", lambda *a, **k: [])

    result = _run(str(tmp_path / "out"), "--experiment-id", "12", "--wave-number", "9")

    assert result.exit_code != 0
    assert "--wave-number" in result.output


# --------------------------------------------------------------------------- #
# Manifest guards
# --------------------------------------------------------------------------- #


def test_re_running_the_same_selection_resumes(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(pd, "download_images", lambda *a, **k: pd.DownloadResult([]))

    out = tmp_path / "out"
    assert _run(str(out), "--experiment-id", "12", "--meta-only").exit_code == 0
    assert _run(str(out), "--experiment-id", "12", "--meta-only").exit_code == 0


def test_a_different_selection_in_the_same_directory_is_refused(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(pd, "download_images", lambda *a, **k: pd.DownloadResult([]))

    out = tmp_path / "out"
    _run(str(out), "--experiment-id", "12", "--meta-only")
    result = _run(str(out), "--experiment-id", "99", "--meta-only")

    assert result.exit_code != 0
    assert "experiment_id" in result.output


def test_images_without_a_manifest_are_refused(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)

    out = tmp_path / "out"
    (out / "images").mkdir(parents=True)

    result = _run(str(out), "--experiment-id", "12")

    assert result.exit_code != 0
    assert pd.MANIFEST_NAME in result.output


# --------------------------------------------------------------------------- #
# Exit codes
# --------------------------------------------------------------------------- #


def test_a_clean_run_exits_zero_and_names_the_log(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(
        pd,
        "download_images",
        lambda *a, **k: pd.DownloadResult(
            [pd.FrameResult(1, "c0", "gravi/1.jpg", ok=True)]
        ),
    )

    out = tmp_path / "out"
    result = _run(str(out), "--experiment-id", "12")

    assert result.exit_code == 0
    assert (out / "download_log.txt").exists()


def test_a_partial_run_exits_non_zero(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(
        pd,
        "download_images",
        lambda *a, **k: pd.DownloadResult(
            [pd.FrameResult(1, "c0", "gravi/1.jpg", ok=False, error="boom")]
        ),
    )

    out = tmp_path / "out"
    result = _run(str(out), "--experiment-id", "12")

    assert result.exit_code != 0
    assert "download_log.txt" in result.output


def test_the_log_names_captures_not_frames(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(
        pd,
        "download_images",
        lambda *a, **k: pd.DownloadResult(
            [pd.FrameResult(1, "c0", "gravi/1.jpg", ok=True)]
        ),
    )

    out = tmp_path / "out"
    _run(str(out), "--experiment-id", "12")

    log = (out / "download_log.txt").read_text()
    assert "capture=" in log and "frame" not in log


def test_scans_without_images_are_called_out(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    _one_scan(monkeypatch)
    monkeypatch.setattr(
        pd,
        "download_images",
        lambda *a, **k: pd.DownloadResult(
            [pd.FrameResult(1, None, "", ok=False, error="no image", no_frames=True)]
        ),
    )

    out = tmp_path / "out"
    result = _run(str(out), "--experiment-id", "12")

    assert result.exit_code == 0
    assert "no image" in result.stderr.lower() or "no images" in result.stderr.lower()


def test_scan_id_selector_fetches_one_scan(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    monkeypatch.setattr(pd, "fetch_plate_scan", lambda c, scan_id: SCAN)
    monkeypatch.setattr(pd, "fetch_plate_images", lambda c, ids: {1: IMAGE})
    monkeypatch.setattr(pd, "fetch_plate_sections", lambda c, ids: [])
    monkeypatch.setattr(pd, "download_images", lambda *a, **k: pd.DownloadResult([]))

    out = tmp_path / "out"
    result = _run(str(out), "--scan-id", "1", "--meta-only")

    assert result.exit_code == 0
    assert (out / "plates.csv").exists()


def test_an_unknown_scan_id_exits_non_zero(tmp_path, monkeypatch):
    _signed_in(monkeypatch)
    monkeypatch.setattr(pd, "fetch_plate_scan", lambda c, scan_id: None)

    result = _run(str(tmp_path / "out"), "--scan-id", "404")

    assert result.exit_code != 0
    assert "404" in result.output


def test_the_retry_hint_names_captures_on_a_plate_run(tmp_path, monkeypatch):
    """Driven through the command, because the noun is chosen where the command builds the
    reporter — a test that builds its own cannot see that choice."""
    from test_plate_download_images import _Client

    client = _Client(fail_on=[IMAGE["object_path"]])
    _signed_in(monkeypatch, client=client)
    _one_scan(monkeypatch)

    res = _run(str(tmp_path / "out"), "--experiment-id", "12")

    assert "Some captures are failing" in res.output
    assert "frames" not in res.output
