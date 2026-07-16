"""`bloomctl cyl download-for-predict` — sidecar helpers + command wiring (mocked client)."""

import hashlib
import json

import pytest
import sleap_roots_contracts
from click.testing import CliRunner
from test_download_metadata import SCAN

import bloomctl.auth as auth
import bloomctl.cyl.download as dl
import bloomctl.cyl.download_for_predict as dfp
from bloomctl.cli import cli
from bloomctl.credentials import Credentials

IMAGES = [
    {"id": 1001, "frame_number": 0, "object_path": "cyl-images/a.png"},
    {"id": 1002, "frame_number": 1, "object_path": "cyl-images/b.png"},
]
FRAME_BYTES = [b"frame-0-bytes", b"frame-1-bytes"]
PARAMS = {"species": "pennycress", "mode": "cylinder", "age": 14}


class _FakeBucket:
    def __init__(self, responses=None):
        self._responses = responses or {}

    def download(self, object_path):
        if object_path in self._responses:
            return self._responses[object_path]
        return f"bytes::{object_path}".encode()


class _FakeStorage:
    def __init__(self, responses=None):
        self._bucket = _FakeBucket(responses)

    def from_(self, bucket):
        assert bucket == "images"
        return self._bucket


class _FakeClient:
    def __init__(self, responses=None):
        self.storage = _FakeStorage(responses)


# --- 3.x oracle / acceptance test -------------------------------------------


def test_oracle_sidecar_is_accepted_by_discover_scans(tmp_path):
    """Manual, dev-machine only (see tasks.md §3 note) — self-skips in CI."""
    sleap_roots_predict = pytest.importorskip("sleap_roots_predict")
    predict_batch = pytest.importorskip("sleap_roots_predict.batch")

    assert dfp._IMAGE_EXTENSIONS == frozenset(predict_batch._IMAGE_EXTENSIONS)

    scan_dir = tmp_path / "scan_1"
    scan_dir.mkdir()
    for image, data in zip(IMAGES, FRAME_BYTES):
        dest = dfp.frame_dest_for_predict(scan_dir, image)
        dest.write_bytes(data)

    sidecar = dfp.build_sidecar(SCAN, IMAGES, FRAME_BYTES, PARAMS)
    assert sidecar["scan_key"] == "scan_1"
    assert set(sidecar["params"]) == {"species", "mode", "age"}
    assert sidecar["params"]["mode"] == "cylinder"
    assert sidecar["image_ids"] == [1001, 1002]
    assert sidecar["images_checksum"].startswith("sha256:")

    dfp.write_sidecar(sidecar, scan_dir / "scan_1.scan_metadata.json")

    scans = sleap_roots_predict.discover_scans(tmp_path)
    assert len(scans) == 1
    assert scans[0].scan_key == "scan_1"
    assert scans[0].error is None
    assert scans[0].params is not None


# --- 4.x pure helpers --------------------------------------------------------


def test_scan_key_for_integer_and_string():
    assert dfp.scan_key_for(1) == "scan_1"
    assert dfp.scan_key_for("1") == "scan_1"


def test_frame_dest_for_predict_uses_frame_number_and_extension(tmp_path):
    dest = dfp.frame_dest_for_predict(tmp_path, IMAGES[0])
    assert dest == tmp_path / "0.png"


def test_frame_dest_for_predict_defaults_to_png_when_extension_missing(tmp_path):
    image = {"frame_number": 3, "object_path": "cyl-images/no-extension"}
    dest = dfp.frame_dest_for_predict(tmp_path, image)
    assert dest == tmp_path / "3.png"


def test_frame_dest_for_predict_raises_on_missing_object_path(tmp_path):
    with pytest.raises(KeyError):
        dfp.frame_dest_for_predict(tmp_path, {"frame_number": 3})


def test_compute_checksum_is_sha256_prefixed_and_order_sensitive():
    checksum = dfp.compute_checksum(FRAME_BYTES)
    assert checksum.startswith("sha256:")
    expected = hashlib.sha256(b"".join(FRAME_BYTES)).hexdigest()
    assert checksum == f"sha256:{expected}"
    assert dfp.compute_checksum(list(reversed(FRAME_BYTES))) != checksum


def test_compute_checksum_empty_list_is_well_defined():
    assert dfp.compute_checksum([]) == f"sha256:{hashlib.sha256(b'').hexdigest()}"


def test_build_sidecar_assembles_all_fields_in_input_order():
    sidecar = dfp.build_sidecar(SCAN, IMAGES, FRAME_BYTES, PARAMS)
    assert set(sidecar) == {"scan_key", "params", "image_ids", "images_checksum"}
    assert sidecar["scan_key"] == dfp.scan_key_for(SCAN["scan_id"])
    assert sidecar["image_ids"] == [1001, 1002]
    assert sidecar["params"] == PARAMS


def test_resolve_sidecar_params_passes_mode_override(monkeypatch):
    captured = {}
    real_resolve_params = sleap_roots_contracts.resolve_params

    def spy(metadata, overrides=None):
        captured["overrides"] = overrides
        return real_resolve_params(metadata, overrides=overrides)

    monkeypatch.setattr(sleap_roots_contracts, "resolve_params", spy)

    params = dfp.resolve_sidecar_params(SCAN)

    assert captured["overrides"] == {"mode": "cylinder"}
    assert params["mode"] == "cylinder"


def test_resolve_sidecar_params_canonicalizes_species_and_age():
    params = dfp.resolve_sidecar_params(SCAN)
    assert params["species"] == "pennycress"
    assert params["age"] == 14


def test_resolve_sidecar_params_raises_valueerror_on_missing_metadata():
    bad_scan = {**SCAN, "species_name": None}
    with pytest.raises(ValueError):
        dfp.resolve_sidecar_params(bad_scan)


def test_resolve_params_ignores_mode_on_pinned_contracts_version():
    """Documents that overrides={"mode": "cylinder"} is not currently load-bearing:
    resolve_params already returns mode="cylinder" with no override at all on the
    pinned sleap-roots-contracts version. If a future bump makes mode
    metadata-driven, this assertion starts failing and flags the divergence."""
    resolved = sleap_roots_contracts.resolve_params(SCAN)
    assert resolved.values["mode"] == "cylinder"


def test_write_sidecar_round_trips_through_json(tmp_path):
    sidecar = dfp.build_sidecar(SCAN, IMAGES, FRAME_BYTES, PARAMS)
    path = tmp_path / "scan_1.scan_metadata.json"
    dfp.write_sidecar(sidecar, path)
    assert json.loads(path.read_text(encoding="utf-8")) == sidecar


def test_write_sidecar_creates_parent_dir(tmp_path):
    sidecar = dfp.build_sidecar(SCAN, IMAGES, FRAME_BYTES, PARAMS)
    path = tmp_path / "nested" / "scan_1.scan_metadata.json"
    dfp.write_sidecar(sidecar, path)
    assert path.exists()


def test_write_sidecar_is_atomic_on_write_failure(tmp_path, monkeypatch):
    from pathlib import Path

    path = tmp_path / "scan_1.scan_metadata.json"
    path.write_text('{"old": "content"}', encoding="utf-8")

    def _boom(self, data, **kwargs):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(Path, "write_text", _boom)

    sidecar = dfp.build_sidecar(SCAN, IMAGES, FRAME_BYTES, PARAMS)
    with pytest.raises(OSError):
        dfp.write_sidecar(sidecar, path)

    assert path.read_text(encoding="utf-8") == '{"old": "content"}'


def test_atomic_write_bytes_is_atomic_on_write_failure(tmp_path, monkeypatch):
    from pathlib import Path

    dest = tmp_path / "0.png"
    dest.write_bytes(b"old-bytes")

    def _boom(self, data):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(Path, "write_bytes", _boom)

    with pytest.raises(OSError):
        dfp._atomic_write_bytes(dest, b"new-bytes")

    assert dest.read_bytes() == b"old-bytes"


def test_validate_frame_numbers_accepts_unique_non_null():
    dfp.validate_frame_numbers(IMAGES)  # must not raise


def test_validate_frame_numbers_rejects_null():
    images = [{"id": 1, "frame_number": None, "object_path": "a.png"}]
    with pytest.raises(ValueError):
        dfp.validate_frame_numbers(images)


def test_validate_frame_numbers_rejects_duplicates():
    images = [
        {"id": 1, "frame_number": 0, "object_path": "a.png"},
        {"id": 2, "frame_number": 0, "object_path": "b.png"},
    ]
    with pytest.raises(ValueError):
        dfp.validate_frame_numbers(images)


def test_clear_scan_dir_removes_existing_contents_and_returns_names(tmp_path):
    scan_dir = tmp_path / "scan_1"
    scan_dir.mkdir()
    (scan_dir / "0.png").write_bytes(b"x")
    (scan_dir / "scan_1.scan_metadata.json").write_text("{}")

    removed = dfp.clear_scan_dir(scan_dir)

    assert not scan_dir.exists()
    assert set(removed) == {"0.png", "scan_1.scan_metadata.json"}


def test_clear_scan_dir_noop_when_absent(tmp_path):
    scan_dir = tmp_path / "scan_1"
    assert dfp.clear_scan_dir(scan_dir) == []
    assert not scan_dir.exists()


# --- 5.x command wiring -------------------------------------------------------


def test_fetch_scan_is_reused_not_duplicated():
    assert dfp.fetch_scan is dl.fetch_scan


def test_fetch_images_is_reused_not_duplicated():
    assert dfp.fetch_images is dl.fetch_images


def test_frame_result_is_reused_not_duplicated():
    assert dfp.FrameResult is dl.FrameResult


def test_download_result_is_reused_not_duplicated():
    assert dfp.DownloadResult is dl.DownloadResult


def _patch_common(monkeypatch, images=None, storage_responses=None):
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _FakeClient(storage_responses))
    monkeypatch.setattr(dfp, "fetch_scan", lambda client, scan_id: SCAN)
    monkeypatch.setattr(
        dfp, "fetch_images", lambda client, scan_id: images if images is not None else IMAGES
    )


def test_cli_happy_path_writes_frames_and_sidecar(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    out = tmp_path / "out"

    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])

    assert result.exit_code == 0, result.output
    assert (out / "scan_1" / "0.png").exists()
    assert (out / "scan_1" / "1.png").exists()
    sidecar = json.loads((out / "scan_1" / "scan_1.scan_metadata.json").read_text())
    assert sidecar["scan_key"] == "scan_1"
    assert sidecar["image_ids"] == [1001, 1002]
    assert sidecar["params"]["mode"] == "cylinder"
    assert sidecar["images_checksum"].startswith("sha256:")


def test_cli_scan_not_found_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _FakeClient())
    monkeypatch.setattr(dfp, "fetch_scan", lambda client, scan_id: None)

    out = tmp_path / "out"
    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "999", str(out)])

    assert result.exit_code != 0
    assert "not found" in result.output.lower()
    assert not out.exists()


def test_cli_missing_species_name_exits_cleanly_without_deleting_existing_dir(
    tmp_path, monkeypatch
):
    bad_scan = {**SCAN, "species_name": None}
    _patch_common(monkeypatch)
    monkeypatch.setattr(dfp, "fetch_scan", lambda client, scan_id: bad_scan)

    out = tmp_path / "out"
    scan_dir = out / "scan_1"
    scan_dir.mkdir(parents=True)
    existing = scan_dir / "existing.png"
    existing.write_bytes(b"must survive a metadata-resolution failure")

    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])

    assert result.exit_code != 0
    assert result.exception is None or not isinstance(result.exception, ValueError)
    assert existing.exists()
    assert existing.read_bytes() == b"must survive a metadata-resolution failure"


def test_cli_duplicate_frame_number_exits_cleanly_without_deleting_existing_dir(
    tmp_path, monkeypatch
):
    dup_images = [
        {"id": 1001, "frame_number": 0, "object_path": "cyl-images/a.png"},
        {"id": 1002, "frame_number": 0, "object_path": "cyl-images/b.png"},
    ]
    _patch_common(monkeypatch, images=dup_images)

    out = tmp_path / "out"
    scan_dir = out / "scan_1"
    scan_dir.mkdir(parents=True)
    existing = scan_dir / "existing.png"
    existing.write_bytes(b"must survive a frame_number validation failure")

    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])

    assert result.exit_code != 0
    assert existing.exists()


def test_cli_partial_frame_failure_no_sidecar_written(tmp_path, monkeypatch):
    def _flaky_download(object_path):
        if object_path == "cyl-images/b.png":
            raise ConnectionError("simulated storage failure")
        return f"bytes::{object_path}".encode()

    _patch_common(monkeypatch)
    out = tmp_path / "out"

    class _FlakyClient(_FakeClient):
        def __init__(self):
            super().__init__()
            self.storage._bucket.download = _flaky_download

    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _FlakyClient())

    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])

    assert result.exit_code != 0
    assert "1" in result.output
    assert (out / "scan_1" / "0.png").exists()
    assert not (out / "scan_1" / "scan_1.scan_metadata.json").exists()


def test_cli_storage_none_response_is_treated_as_failure(tmp_path, monkeypatch):
    _patch_common(monkeypatch, storage_responses={"cyl-images/b.png": None})
    out = tmp_path / "out"

    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])

    assert result.exit_code != 0
    assert (out / "scan_1" / "0.png").exists()
    assert not (out / "scan_1" / "scan_1.scan_metadata.json").exists()


def test_fetch_scans_experiment_path_never_called(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        dl,
        "fetch_scans",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("fetch_scans (experiment path) must not run for download-for-predict")
        ),
    )

    out = tmp_path / "out"
    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])

    assert result.exit_code == 0, result.output


def test_discover_scans_smoke_after_happy_path(tmp_path, monkeypatch):
    """Manual, dev-machine only (see tasks.md §3 note) — self-skips in CI."""
    sleap_roots_predict = pytest.importorskip("sleap_roots_predict")

    _patch_common(monkeypatch)
    out = tmp_path / "out"
    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])
    assert result.exit_code == 0, result.output

    scans = sleap_roots_predict.discover_scans(out)
    assert len(scans) == 1
    assert scans[0].error is None


def test_cli_registration_shows_in_help():
    result = CliRunner().invoke(cli, ["cyl", "--help"])
    assert "download-for-predict" in result.output


def test_cli_missing_credentials_hints_login(tmp_path, monkeypatch):
    import bloomctl.credentials as creds

    monkeypatch.setattr(creds, "default_config_dir", lambda: tmp_path / ".bloom")

    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(tmp_path / "out")])

    assert result.exit_code != 0
    assert "login" in result.output.lower()


def test_cli_zero_frame_scan_exits_nonzero(tmp_path, monkeypatch):
    _patch_common(monkeypatch, images=[])
    out = tmp_path / "out"

    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])

    assert result.exit_code != 0
    assert "no frames found" in result.output.lower()
    assert not out.exists()


def test_cli_profile_option_passed_through(tmp_path, monkeypatch):
    captured = {}

    def fake_load_credentials(profile):
        captured["profile"] = profile
        return Credentials("https://x/api", "KEY", "u@s.edu", "pw")

    monkeypatch.setattr("bloomctl.credentials.load_credentials", fake_load_credentials)
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _FakeClient())
    monkeypatch.setattr(dfp, "fetch_scan", lambda client, scan_id: SCAN)
    monkeypatch.setattr(dfp, "fetch_images", lambda client, scan_id: IMAGES)

    out = tmp_path / "out"
    result = CliRunner().invoke(
        cli, ["cyl", "download-for-predict", "1", str(out), "-p", "staging"]
    )

    assert result.exit_code == 0, result.output
    assert captured["profile"] == "staging"


def test_checksum_changes_when_frame_content_changes(tmp_path, monkeypatch):
    _patch_common(monkeypatch, storage_responses={"cyl-images/a.png": b"version-1"})
    out_a = tmp_path / "out_a"
    result_a = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out_a)])
    assert result_a.exit_code == 0, result_a.output
    sidecar_a = json.loads((out_a / "scan_1" / "scan_1.scan_metadata.json").read_text())

    _patch_common(monkeypatch, storage_responses={"cyl-images/a.png": b"version-2-different"})
    out_b = tmp_path / "out_b"
    result_b = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out_b)])
    assert result_b.exit_code == 0, result_b.output
    sidecar_b = json.loads((out_b / "scan_1" / "scan_1.scan_metadata.json").read_text())

    assert sidecar_a["images_checksum"] != sidecar_b["images_checksum"]


def test_stale_frame_cleared_on_successful_retry(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    out = tmp_path / "out"
    scan_dir = out / "scan_1"
    scan_dir.mkdir(parents=True)
    stale = scan_dir / "2.png"
    stale.write_bytes(b"leftover from a failed attempt whose cyl_images row is now gone")

    result = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])

    assert result.exit_code == 0, result.output
    assert not stale.exists()
    assert (scan_dir / "0.png").exists()
    assert (scan_dir / "1.png").exists()


def test_cli_stale_sidecar_does_not_survive_a_partial_failure_retry(tmp_path, monkeypatch):
    _patch_common(monkeypatch)
    out = tmp_path / "out"

    result1 = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])
    assert result1.exit_code == 0, result1.output
    sidecar_path = out / "scan_1" / "scan_1.scan_metadata.json"
    assert sidecar_path.exists()

    def _flaky_download(object_path):
        if object_path == "cyl-images/b.png":
            raise ConnectionError("simulated storage failure")
        return f"bytes::{object_path}::changed".encode()

    class _FlakyClient(_FakeClient):
        def __init__(self):
            super().__init__()
            self.storage._bucket.download = _flaky_download

    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _FlakyClient())

    result2 = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])

    assert result2.exit_code != 0
    assert not sidecar_path.exists()


def test_cli_echoes_what_it_cleared_on_rerun(tmp_path, monkeypatch):
    # NB: assert an exact, deliberately-chosen count/phrase, not a loose substring like
    # "clear" — pytest truncates tmp_path's directory name to 30 chars of the test's own
    # name, and that truncated path is embedded in the CLI's own path-containing output, so
    # a generic word can coincidentally "pass" via the directory name rather than real output.
    _patch_common(monkeypatch)
    out = tmp_path / "out"

    result1 = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])
    assert result1.exit_code == 0, result1.output
    assert "existing file" not in result1.output.lower()

    result2 = CliRunner().invoke(cli, ["cyl", "download-for-predict", "1", str(out)])
    assert result2.exit_code == 0, result2.output
    # 0.png, 1.png, scan_1.scan_metadata.json from the first run.
    assert "3 existing file" in result2.output.lower()
