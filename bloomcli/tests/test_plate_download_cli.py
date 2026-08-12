"""`bloomctl plate download` — the command surface: selectors, name resolution, guards, exits."""

from __future__ import annotations

from click.testing import CliRunner
from test_plate_download_paths import IMAGE, SCAN

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
        raise APIError({"message": "search query too long (max 200 characters)"})

    monkeypatch.setattr(pd, "search_experiments", _boom)
    result = _run(str(tmp_path / "out"), "--experiment-name", "x" * 201)
    assert result.exit_code != 0
    assert "too long" in result.output


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
    assert "capture=" in log and "frame=" not in log


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
