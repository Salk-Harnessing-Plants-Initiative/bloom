"""`bloomctl cyl download-for-predict` — sidecar helpers + command wiring (mocked client)."""

import hashlib
import json
import re
from pathlib import Path

import pytest
import sleap_roots_contracts
from click.testing import CliRunner
from sleap_roots_contracts import RUN_MANIFEST_FILENAME
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
    assert sidecar["image_ids"] == ["1001", "1002"]
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
    assert sidecar["image_ids"] == ["1001", "1002"]
    assert sidecar["params"] == PARAMS


def test_build_sidecar_image_ids_and_checksum_validate_as_input_ref():
    """Regression for bloom#555: `image_ids` must be `list[str]` — the exact shape
    `sleap_roots_contracts.InputRef` (and, in turn, trait_extractor's `ScanMetadata`)
    requires. Validating against the real contract model, not just a literal list of
    strings, catches this class of type bug even if the literal expectation above is
    ever loosened."""
    from sleap_roots_contracts import InputRef

    sidecar = dfp.build_sidecar(SCAN, IMAGES, FRAME_BYTES, PARAMS)

    input_ref = InputRef.model_validate(
        {"image_ids": sidecar["image_ids"], "images_checksum": sidecar["images_checksum"]}
    )
    assert input_ref.image_ids == ["1001", "1002"]


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

    dest = tmp_path / "0.png"
    dest.write_bytes(b"old-bytes")

    from pathlib import Path

    monkeypatch.setattr(
        Path, "write_bytes", lambda self, data: (_ for _ in ()).throw(OSError("crash mid-write"))
    )

    with pytest.raises(OSError):
        dfp.atomic_write_bytes(dest, b"new-bytes")

    assert dest.read_bytes() == b"old-bytes"
    assert list(tmp_path.glob(".dl-*")) == []  # and no temp file left behind


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
    assert sidecar["image_ids"] == ["1001", "1002"]
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


# --- batch: pure helpers ------------------------------------------------------


def test_read_scan_ids_from_file(tmp_path):
    path = tmp_path / "scan_ids.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert dfp.read_scan_ids(str(path)) == [1, 2, 3]


def test_read_scan_ids_empty_array_is_valid(tmp_path):
    path = tmp_path / "scan_ids.json"
    path.write_text("[]", encoding="utf-8")
    assert dfp.read_scan_ids(str(path)) == []


def test_read_scan_ids_from_stdin():
    import io

    assert dfp.read_scan_ids("-", stdin=io.StringIO("[1, 2]")) == [1, 2]


def test_read_scan_ids_missing_path_raises(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        dfp.read_scan_ids(str(tmp_path / "nope.json"))


def test_read_scan_ids_directory_path_raises(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        dfp.read_scan_ids(str(tmp_path))


def test_read_scan_ids_non_json_raises(tmp_path):
    path = tmp_path / "scan_ids.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        dfp.read_scan_ids(str(path))


@pytest.mark.parametrize("content", ['{"a": 1}', '"just a string"', "42", "[1, 2.5]", '[1, "x"]'])
def test_read_scan_ids_non_integer_array_raises(tmp_path, content):
    path = tmp_path / "scan_ids.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="array of integers"):
        dfp.read_scan_ids(str(path))


def test_parse_scan_ids_flag_comma_separated():
    assert dfp.parse_scan_ids_flag("1,2,3") == [1, 2, 3]


def test_parse_scan_ids_flag_tolerates_spaces():
    assert dfp.parse_scan_ids_flag(" 1, 2 , 3 ") == [1, 2, 3]


def test_parse_scan_ids_flag_rejects_non_numeric():
    with pytest.raises(ValueError, match="integers"):
        dfp.parse_scan_ids_flag("1,abc,3")


def test_scan_is_already_staged_true_for_valid_sidecar(tmp_path):
    scan_dir = tmp_path / "scan_1"
    scan_dir.mkdir()
    (scan_dir / "scan_1.scan_metadata.json").write_text(
        json.dumps({"scan_key": "scan_1"}), encoding="utf-8"
    )
    assert dfp.scan_is_already_staged(scan_dir, "scan_1") is True


def test_scan_is_already_staged_false_when_missing(tmp_path):
    assert dfp.scan_is_already_staged(tmp_path / "scan_1", "scan_1") is False


def test_scan_is_already_staged_false_when_unparseable(tmp_path):
    scan_dir = tmp_path / "scan_1"
    scan_dir.mkdir()
    (scan_dir / "scan_1.scan_metadata.json").write_text("{ not json", encoding="utf-8")
    assert dfp.scan_is_already_staged(scan_dir, "scan_1") is False


def test_scan_is_already_staged_false_when_scan_key_mismatched(tmp_path):
    scan_dir = tmp_path / "scan_1"
    scan_dir.mkdir()
    (scan_dir / "scan_1.scan_metadata.json").write_text(
        json.dumps({"scan_key": "scan_999"}), encoding="utf-8"
    )
    assert dfp.scan_is_already_staged(scan_dir, "scan_1") is False


def test_scan_is_already_staged_false_when_image_ids_are_int(tmp_path):
    """Regression for bloom#555: a sidecar staged by the pre-fix build_sidecar() has
    int image_ids. Without this check, scan_is_already_staged would treat it as valid
    forever, and the batch resume path would never re-stage it with corrected str ids."""
    scan_dir = tmp_path / "scan_1"
    scan_dir.mkdir()
    (scan_dir / "scan_1.scan_metadata.json").write_text(
        json.dumps({"scan_key": "scan_1", "image_ids": [1001, 1002]}), encoding="utf-8"
    )
    assert dfp.scan_is_already_staged(scan_dir, "scan_1") is False


def test_scan_is_already_staged_true_when_image_ids_are_str(tmp_path):
    scan_dir = tmp_path / "scan_1"
    scan_dir.mkdir()
    (scan_dir / "scan_1.scan_metadata.json").write_text(
        json.dumps({"scan_key": "scan_1", "image_ids": ["1001", "1002"]}), encoding="utf-8"
    )
    assert dfp.scan_is_already_staged(scan_dir, "scan_1") is True


def _fetch_scan_for(scan_id_to_images):
    """A fetch_scan stand-in returning a per-scan_id SCAN row (scan_id field matches).

    Every scan_id "exists" (returns a row) — `scan_id_to_images` only customizes each scan's
    image list (default IMAGES for any id not listed); it does not model a not-found scan (tests
    for that override `fetch_scan` directly).
    """

    def _fetch_scan(client, scan_id):
        return {**SCAN, "scan_id": scan_id}

    return _fetch_scan


def _fetch_images_for(scan_id_to_images):
    def _fetch_images(client, scan_id):
        return scan_id_to_images.get(scan_id, IMAGES)

    return _fetch_images


def _patch_batch(monkeypatch, scan_id_to_images=None, storage_responses=None):
    """Batch-test analog of `_patch_common`: fetch_scan/fetch_images vary by scan_id.

    `scan_id_to_images` maps scan_id -> its images list (default IMAGES for any id not listed);
    an empty list simulates a "no frames found" failure for that scan_id.
    """
    scan_id_to_images = scan_id_to_images or {}
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _FakeClient(storage_responses))
    monkeypatch.setattr(dfp, "fetch_scan", _fetch_scan_for(scan_id_to_images))
    monkeypatch.setattr(dfp, "fetch_images", _fetch_images_for(scan_id_to_images))


def test_stage_one_scan_success(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    client = auth.make_authed_client(None)
    result = dfp.stage_one_scan(client, 1, tmp_path)
    assert result.status == "ok"
    assert result.scan_key == "scan_1"
    assert (tmp_path / "scan_1" / "scan_1.scan_metadata.json").exists()


def test_stage_one_scan_not_found(monkeypatch, tmp_path):
    _patch_batch(monkeypatch, scan_id_to_images={})
    monkeypatch.setattr(dfp, "fetch_scan", lambda client, scan_id: None)
    client = auth.make_authed_client(None)
    result = dfp.stage_one_scan(client, 999, tmp_path)
    assert result.status == "failed"
    assert "not found" in result.error.lower()


def test_stage_one_scan_zero_frames(monkeypatch, tmp_path):
    _patch_batch(monkeypatch, scan_id_to_images={1: []})
    client = auth.make_authed_client(None)
    result = dfp.stage_one_scan(client, 1, tmp_path)
    assert result.status == "failed"
    assert "no frames found" in result.error.lower()


def test_stage_one_scan_invalid_frame_numbers(monkeypatch, tmp_path):
    dup_images = [
        {"id": 1001, "frame_number": 0, "object_path": "cyl-images/a.png"},
        {"id": 1002, "frame_number": 0, "object_path": "cyl-images/b.png"},
    ]
    _patch_batch(monkeypatch, scan_id_to_images={1: dup_images})
    client = auth.make_authed_client(None)
    result = dfp.stage_one_scan(client, 1, tmp_path)
    assert result.status == "failed"


def test_stage_one_scan_metadata_resolution_failure(monkeypatch, tmp_path):
    _patch_batch(monkeypatch)

    def _bad_scan(client, scan_id):
        return {**SCAN, "scan_id": scan_id, "species_name": None}

    monkeypatch.setattr(dfp, "fetch_scan", _bad_scan)
    client = auth.make_authed_client(None)
    result = dfp.stage_one_scan(client, 1, tmp_path)
    assert result.status == "failed"


def test_stage_one_scan_partial_frame_failure(monkeypatch, tmp_path):
    def _flaky_download(object_path):
        if object_path == "cyl-images/b.png":
            raise ConnectionError("simulated storage failure")
        return f"bytes::{object_path}".encode()

    _patch_batch(monkeypatch)

    class _FlakyClient(_FakeClient):
        def __init__(self):
            super().__init__()
            self.storage._bucket.download = _flaky_download

    client = _FlakyClient()
    result = dfp.stage_one_scan(client, 1, tmp_path)
    assert result.status == "failed"
    assert "frames failed to download" in result.error
    assert not (tmp_path / "scan_1" / "scan_1.scan_metadata.json").exists()


def test_stage_one_scan_isolates_unexpected_network_error(monkeypatch, tmp_path):
    """A transient network/auth error from fetch_scan/fetch_images (not just the ValueError
    validation cases already handled) must be isolated into a failed ScanResult, never raised —
    review finding: this was previously uncaught and would crash the whole batch."""
    _patch_batch(monkeypatch)

    def _boom(client, scan_id):
        raise ConnectionError("simulated transient network failure")

    monkeypatch.setattr(dfp, "fetch_scan", _boom)
    result = dfp.stage_one_scan(object(), 1, tmp_path)
    assert result.status == "failed"
    assert result.scan_key == "scan_1"
    assert "simulated transient network failure" in result.error


def test_stage_one_scan_isolates_unexpected_error_from_fetch_images(monkeypatch, tmp_path):
    _patch_batch(monkeypatch)

    def _boom(client, scan_id):
        raise RuntimeError("simulated auth token expiry")

    monkeypatch.setattr(dfp, "fetch_images", _boom)
    result = dfp.stage_one_scan(object(), 1, tmp_path)
    assert result.status == "failed"
    assert "simulated auth token expiry" in result.error


def test_batch_cli_isolates_unexpected_network_error_among_several(tmp_path, monkeypatch):
    """The batch command itself must not crash if one scan's fetch raises something other
    than the already-handled cases — the other scans must still be processed and reported."""
    _patch_batch(monkeypatch)

    def _flaky_fetch_scan(client, scan_id):
        if scan_id == 2:
            raise ConnectionError("simulated transient network failure")
        return {**SCAN, "scan_id": scan_id}

    monkeypatch.setattr(dfp, "fetch_scan", _flaky_fetch_scan)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2, 3]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code != 0
    assert (out / "scan_1" / "scan_1.scan_metadata.json").exists()
    assert (out / "scan_3" / "scan_3.scan_metadata.json").exists()
    assert "scan_2" in result.output
    assert "simulated transient network failure" in result.output


def test_stage_one_scan_already_staged_is_skipped(tmp_path, monkeypatch):
    scan_dir = tmp_path / "scan_1"
    scan_dir.mkdir()
    (scan_dir / "scan_1.scan_metadata.json").write_text(
        json.dumps({"scan_key": "scan_1"}), encoding="utf-8"
    )

    def _boom(client, scan_id):
        raise AssertionError("fetch_scan must not be called for an already-staged scan")

    monkeypatch.setattr(dfp, "fetch_scan", _boom)
    client = object()
    result = dfp.stage_one_scan(client, 1, tmp_path)
    assert result.status == "skipped"


def test_stage_one_scan_malformed_sidecar_triggers_full_redownload(tmp_path, monkeypatch):
    """Review finding: `scan_is_already_staged` returning False for a malformed/mismatched
    sidecar was only unit-tested at the helper level — nothing confirmed `stage_one_scan`
    actually goes on to clear and successfully redownload, end to end."""
    scan_dir = tmp_path / "scan_1"
    scan_dir.mkdir()
    (scan_dir / "scan_1.scan_metadata.json").write_text("{ not json", encoding="utf-8")
    stale = scan_dir / "0.png"
    stale.write_bytes(b"stale content from a previous run's malformed sidecar")

    _patch_batch(monkeypatch)
    client = _FakeClient()
    result = dfp.stage_one_scan(client, 1, tmp_path)

    assert result.status == "ok"
    sidecar_path = scan_dir / "scan_1.scan_metadata.json"
    assert json.loads(sidecar_path.read_text(encoding="utf-8"))["scan_key"] == "scan_1"
    assert stale.read_bytes() != b"stale content from a previous run's malformed sidecar"


def test_stage_one_scan_stale_int_image_ids_sidecar_triggers_full_redownload(tmp_path, monkeypatch):
    """Regression for bloom#555: a scan staged by the pre-fix build_sidecar() (int
    image_ids) must be treated as not-staged and re-downloaded, not silently skipped
    forever by the batch resume path."""
    scan_dir = tmp_path / "scan_1"
    scan_dir.mkdir()
    (scan_dir / "scan_1.scan_metadata.json").write_text(
        json.dumps({"scan_key": "scan_1", "image_ids": [1001, 1002]}), encoding="utf-8"
    )

    _patch_batch(monkeypatch)
    client = _FakeClient()
    result = dfp.stage_one_scan(client, 1, tmp_path)

    assert result.status == "ok"
    sidecar = json.loads((scan_dir / "scan_1.scan_metadata.json").read_text(encoding="utf-8"))
    assert sidecar["image_ids"] == ["1001", "1002"]


def test_batch_cli_malformed_sidecar_triggers_full_redownload(tmp_path, monkeypatch):
    out = tmp_path / "out"
    scan_dir = out / "scan_1"
    scan_dir.mkdir(parents=True)
    (scan_dir / "scan_1.scan_metadata.json").write_text(
        json.dumps({"scan_key": "scan_999"}), encoding="utf-8"
    )

    _patch_batch(monkeypatch)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code == 0, result.output
    sidecar = json.loads((scan_dir / "scan_1.scan_metadata.json").read_text())
    assert sidecar["scan_key"] == "scan_1"


# --- batch: command wiring -----------------------------------------------------


def test_batch_cli_happy_path(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2, 3]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code == 0, result.output
    for scan_id in (1, 2, 3):
        assert (out / f"scan_{scan_id}" / f"scan_{scan_id}.scan_metadata.json").exists()


def test_batch_cli_json_all_ok(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2, 3]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file), "--json"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 3
    assert all(entry["status"] == "ok" for entry in payload)


def test_batch_cli_isolates_one_bad_scan(tmp_path, monkeypatch):
    """One bad scan among several does not abort the batch (always runs, mocked — no importorskip)."""
    _patch_batch(monkeypatch, scan_id_to_images={2: []})
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2, 3]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code != 0
    assert (out / "scan_1" / "scan_1.scan_metadata.json").exists()
    assert (out / "scan_3" / "scan_3.scan_metadata.json").exists()
    assert not (out / "scan_2").exists()
    assert "scan_2" in result.output


def test_batch_cli_isolates_one_bad_scan_json(tmp_path, monkeypatch):
    _patch_batch(monkeypatch, scan_id_to_images={2: []})
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2, 3]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file), "--json"]
    )

    assert result.exit_code != 0
    payload = {entry["scan_key"]: entry for entry in json.loads(result.output)}
    assert payload["scan_1"]["status"] == "ok"
    assert payload["scan_2"]["status"] == "failed"
    assert payload["scan_2"]["error"]
    assert payload["scan_3"]["status"] == "ok"


def test_batch_oracle_discover_scans_accepts_the_survivors(tmp_path, monkeypatch):
    """Manual, dev-machine only — self-skips in CI (mirrors the single-command's oracle test)."""
    sleap_roots_predict = pytest.importorskip("sleap_roots_predict")

    _patch_batch(monkeypatch, scan_id_to_images={2: []})
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2, 3]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )
    assert result.exit_code != 0

    scans = sleap_roots_predict.discover_scans(out)
    assert {s.scan_key for s in scans} == {"scan_1", "scan_3"}
    assert all(s.error is None for s in scans)


def test_batch_cli_empty_array_is_noop(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code == 0, result.output
    assert not out.exists()


def test_batch_cli_malformed_scan_ids_source_makes_no_call(tmp_path, monkeypatch):
    called = {"auth": False}
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: called.__setitem__("auth", True) or Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    bad = tmp_path / "scan_ids.json"
    bad.write_text("{ not json", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(bad)])

    assert result.exit_code != 0
    assert not called["auth"]
    assert not out.exists()


def test_batch_cli_nonexistent_scan_ids_source_makes_no_call(tmp_path, monkeypatch):
    called = {"auth": False}
    monkeypatch.setattr(
        "bloomctl.credentials.load_credentials",
        lambda *a, **k: called.__setitem__("auth", True) or Credentials("https://x/api", "KEY", "u@s.edu", "pw"),
    )
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(tmp_path / "nope.json")]
    )

    assert result.exit_code != 0
    assert not called["auth"]


def test_batch_cli_stdin(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", "-"], input="[1, 2]"
    )

    assert result.exit_code == 0, result.output
    assert (out / "scan_1").exists()
    assert (out / "scan_2").exists()


def test_batch_cli_scan_ids_flag(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids", "1,2"]
    )

    assert result.exit_code == 0, result.output
    assert (out / "scan_1").exists()
    assert (out / "scan_2").exists()


def test_batch_cli_source_and_flag_both_given_is_usage_error(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli,
        ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file), "--scan-ids", "1"],
    )

    assert result.exit_code != 0
    assert not out.exists()


def test_batch_cli_source_and_flag_both_omitted_is_usage_error(tmp_path):
    out = tmp_path / "out"

    result = CliRunner().invoke(cli, ["cyl", "batch-download-for-predict", str(out)])

    assert result.exit_code != 0
    assert not out.exists()


def test_batch_cli_already_staged_scan_is_skipped_not_redownloaded(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    out = tmp_path / "out"
    scan_dir = out / "scan_1"
    scan_dir.mkdir(parents=True)
    (scan_dir / "scan_1.scan_metadata.json").write_text(
        json.dumps({"scan_key": "scan_1"}), encoding="utf-8"
    )

    def _boom_download(object_path):
        raise AssertionError("must not download frames for an already-staged scan")

    class _AssertingClient(_FakeClient):
        def __init__(self):
            super().__init__()
            self.storage._bucket.download = _boom_download

    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _AssertingClient())
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(
        CliRunner()
        .invoke(cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file), "--json"])
        .output
    )
    assert payload[0]["status"] == "skipped"


def _mixed_status_setup(base_dir):
    """A fresh out_dir + scan_ids.json for a 1-ok/1-skipped/1-failed batch (scan_ids.json: [1,2,3])."""
    out = base_dir / "out"
    scan_dir_2 = out / "scan_2"
    scan_dir_2.mkdir(parents=True)
    (scan_dir_2 / "scan_2.scan_metadata.json").write_text(
        json.dumps({"scan_key": "scan_2"}), encoding="utf-8"
    )
    ids_file = base_dir / "scan_ids.json"
    ids_file.write_text("[1, 2, 3]", encoding="utf-8")
    return out, ids_file


def test_batch_cli_mixed_statuses_json_output(tmp_path, monkeypatch):
    _patch_batch(monkeypatch, scan_id_to_images={3: []})
    out, ids_file = _mixed_status_setup(tmp_path)

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file), "--json"]
    )
    assert result.exit_code != 0
    payload = {entry["scan_key"]: entry["status"] for entry in json.loads(result.output)}
    assert payload == {"scan_1": "ok", "scan_2": "skipped", "scan_3": "failed"}


def test_batch_cli_mixed_statuses_default_output(tmp_path, monkeypatch):
    _patch_batch(monkeypatch, scan_id_to_images={3: []})
    out, ids_file = _mixed_status_setup(tmp_path)

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )
    assert result.exit_code != 0
    assert "1 skipped" in result.output.lower()
    assert "1 failed" in result.output.lower()
    assert "scan_3" in result.output


def test_batch_cli_profile_option_passed_through(tmp_path, monkeypatch):
    captured = {}

    def fake_load_credentials(profile):
        captured["profile"] = profile
        return Credentials("https://x/api", "KEY", "u@s.edu", "pw")

    monkeypatch.setattr("bloomctl.credentials.load_credentials", fake_load_credentials)
    monkeypatch.setattr(auth, "make_authed_client", lambda creds: _FakeClient())
    monkeypatch.setattr(dfp, "fetch_scan", _fetch_scan_for({1: IMAGES}))
    monkeypatch.setattr(dfp, "fetch_images", _fetch_images_for({1: IMAGES}))

    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli,
        ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file), "-p", "staging"],
    )

    assert result.exit_code == 0, result.output
    assert captured["profile"] == "staging"


def test_batch_cli_registration_shows_in_help():
    result = CliRunner().invoke(cli, ["cyl", "--help"])
    assert "batch-download-for-predict" in result.output


# --- per-scan lock (bloom #653/#533) -------------------------------------------


def _write_lock(out_dir, scan_key, *, acquired_at, pid=999):
    lock_path = out_dir / ".locks" / f"{scan_key}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": pid, "acquired_at": acquired_at}), encoding="utf-8")
    return lock_path


def test_stage_one_scan_lock_contention_reports_failed_naming_pid_and_age(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    import time as time_module

    _write_lock(tmp_path, "scan_1", acquired_at=time_module.time())

    def _boom(client, scan_id):
        raise AssertionError("fetch_scan must not be called while the scan's lock is contended")

    monkeypatch.setattr(dfp, "fetch_scan", _boom)

    client = auth.make_authed_client(None)
    result = dfp.stage_one_scan(client, 1, tmp_path)

    assert result.status == "failed"
    assert "999" in result.error
    assert not (tmp_path / "scan_1" / "scan_1.scan_metadata.json").exists()


def test_stage_one_scan_stale_lock_is_reclaimed_and_stages_normally(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    import time as time_module

    _write_lock(tmp_path, "scan_1", acquired_at=time_module.time() - 10_000)

    client = auth.make_authed_client(None)
    result = dfp.stage_one_scan(client, 1, tmp_path)

    assert result.status == "ok"
    assert (tmp_path / "scan_1" / "scan_1.scan_metadata.json").exists()


def test_stage_one_scan_lock_released_after_success(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    client = auth.make_authed_client(None)
    dfp.stage_one_scan(client, 1, tmp_path)
    assert not (tmp_path / ".locks" / "scan_1.lock").exists()


def test_batch_cli_lock_contention_isolates_one_scan_others_succeed(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    import time as time_module

    out = tmp_path / "out"
    _write_lock(out, "scan_1", acquired_at=time_module.time())
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2]", encoding="utf-8")

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code != 0
    assert "scan_1" in result.output
    assert (out / "scan_2" / "scan_2.scan_metadata.json").exists()
    assert not (out / "scan_1" / "scan_1.scan_metadata.json").exists()


def test_stage_one_scan_first_ever_out_dir_has_no_locks_directory_yet(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    out = tmp_path / "brand_new_out_dir"
    assert not out.exists()

    client = auth.make_authed_client(None)
    result = dfp.stage_one_scan(client, 1, out)

    assert result.status == "ok"


# --- RunManifest write + merge (bloom #653) -------------------------------------


def _read_manifest(out_dir):
    return json.loads((out_dir / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8"))


def test_batch_cli_writes_manifest_with_every_staged_scan_key(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2, 3]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code == 0, result.output
    manifest = _read_manifest(out)
    assert sorted(manifest["scan_keys"]) == ["scan_1", "scan_2", "scan_3"]


def test_batch_cli_manifest_excludes_a_scan_that_failed_this_run(tmp_path, monkeypatch):
    _patch_batch(monkeypatch, scan_id_to_images={2: []})
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2, 3]", encoding="utf-8")
    out = tmp_path / "out"

    CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    manifest = _read_manifest(out)
    assert sorted(manifest["scan_keys"]) == ["scan_1", "scan_3"]


def test_batch_cli_manifest_includes_a_skipped_already_staged_scan(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    scan_dir = tmp_path / "out" / "scan_1"
    scan_dir.mkdir(parents=True)
    (scan_dir / "scan_1.scan_metadata.json").write_text(
        json.dumps({"scan_key": "scan_1"}), encoding="utf-8"
    )
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2]", encoding="utf-8")
    out = tmp_path / "out"

    CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    manifest = _read_manifest(out)
    assert sorted(manifest["scan_keys"]) == ["scan_1", "scan_2"]


def test_batch_cli_pipeline_run_id_from_argo_workflow_name(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "wf-abc123")
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")
    out = tmp_path / "out"

    CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert _read_manifest(out)["pipeline_run_id"] == "wf-abc123"


def test_batch_cli_pipeline_run_id_falls_back_to_generated_local_placeholder(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    monkeypatch.delenv("ARGO_WORKFLOW_NAME", raising=False)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code == 0, result.output
    assert re.fullmatch(r"local-[0-9a-f]{8}", _read_manifest(out)["pipeline_run_id"])


def test_batch_cli_two_invocations_without_argo_workflow_name_get_distinguishable_ids(
    tmp_path, monkeypatch
):
    _patch_batch(monkeypatch)
    monkeypatch.delenv("ARGO_WORKFLOW_NAME", raising=False)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")
    out = tmp_path / "out"

    CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )
    first_id = _read_manifest(out)["pipeline_run_id"]

    ids_file2 = tmp_path / "scan_ids2.json"
    ids_file2.write_text("[1]", encoding="utf-8")
    CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file2)]
    )
    second_id = _read_manifest(out)["pipeline_run_id"]

    assert first_id != second_id


def test_batch_cli_second_invocation_merges_disjoint_scan_keys(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    out = tmp_path / "out"

    ids_file_1 = tmp_path / "scan_ids_1.json"
    ids_file_1.write_text("[1, 2]", encoding="utf-8")
    CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file_1)]
    )
    assert sorted(_read_manifest(out)["scan_keys"]) == ["scan_1", "scan_2"]

    ids_file_2 = tmp_path / "scan_ids_2.json"
    ids_file_2.write_text("[3]", encoding="utf-8")
    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file_2)]
    )

    assert result.exit_code == 0, result.output
    assert sorted(_read_manifest(out)["scan_keys"]) == ["scan_1", "scan_2", "scan_3"]


def test_batch_cli_second_invocation_with_overlapping_scan_keys_has_no_duplicates(
    tmp_path, monkeypatch
):
    _patch_batch(monkeypatch)
    out = tmp_path / "out"

    ids_file_1 = tmp_path / "scan_ids_1.json"
    ids_file_1.write_text("[1, 2]", encoding="utf-8")
    CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file_1)]
    )

    ids_file_2 = tmp_path / "scan_ids_2.json"
    ids_file_2.write_text("[2, 3]", encoding="utf-8")
    CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file_2)]
    )

    scan_keys = _read_manifest(out)["scan_keys"]
    assert sorted(scan_keys) == ["scan_1", "scan_2", "scan_3"]
    assert len(scan_keys) == len(set(scan_keys))


def test_batch_cli_manifest_lock_contention_fails_without_corrupting_existing_manifest(
    tmp_path, monkeypatch
):
    _patch_batch(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    (out / RUN_MANIFEST_FILENAME).write_text(
        json.dumps({"schema_version": "1", "pipeline_run_id": "wf-old", "scan_keys": ["scan_9"]}),
        encoding="utf-8",
    )
    import time as time_module

    _write_lock(out, "manifest", acquired_at=time_module.time())

    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code != 0
    # A clean click.ClickException (and a plain ctx.exit()) both normalize to SystemExit via
    # CliRunner — a raw, unhandled exception (e.g. an OSError escaping acquire_lock) would
    # instead surface here as that exception's own instance, not SystemExit. Confirmed
    # empirically: click.ClickException/ctx.exit() -> result.exception is SystemExit(1); an
    # unhandled exception -> result.exception is that exception itself.
    assert isinstance(result.exception, SystemExit)
    assert json.loads((out / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")) == {
        "schema_version": "1",
        "pipeline_run_id": "wf-old",
        "scan_keys": ["scan_9"],
    }


def test_batch_cli_manifest_lock_contention_with_no_existing_manifest_fails_cleanly(
    tmp_path, monkeypatch
):
    _patch_batch(monkeypatch)
    out = tmp_path / "out"
    import time as time_module

    _write_lock(out, "manifest", acquired_at=time_module.time())

    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert not (out / RUN_MANIFEST_FILENAME).exists()


def test_batch_cli_corrupt_existing_manifest_fails_loud_not_silently_discarded(
    tmp_path, monkeypatch
):
    _patch_batch(monkeypatch)
    out = tmp_path / "out"
    out.mkdir()
    (out / RUN_MANIFEST_FILENAME).write_text("{ not json", encoding="utf-8")

    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert (out / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8") == "{ not json"


def test_batch_cli_all_scans_failed_no_prior_manifest_skips_write_no_crash(tmp_path, monkeypatch):
    _patch_batch(monkeypatch, scan_id_to_images={1: []})
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert not (out / RUN_MANIFEST_FILENAME).exists()


def test_batch_cli_stale_manifest_lock_is_reclaimed(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    out = tmp_path / "out"
    import time as time_module

    _write_lock(out, "manifest", acquired_at=time_module.time() - 10_000)

    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code == 0, result.output
    assert sorted(_read_manifest(out)["scan_keys"]) == ["scan_1"]


def test_batch_cli_manifest_lock_released_after_success(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")
    out = tmp_path / "out"

    CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert not (out / ".locks" / "manifest.lock").exists()


def test_batch_cli_lock_staleness_seconds_threads_to_per_scan_lock(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    out = tmp_path / "out"
    import time as time_module

    _write_lock(out, "scan_1", acquired_at=time_module.time() - 10)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "cyl",
            "batch-download-for-predict",
            str(out),
            "--scan-ids-file",
            str(ids_file),
            "--lock-staleness-seconds",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (out / "scan_1" / "scan_1.scan_metadata.json").exists()


def test_batch_cli_lock_staleness_seconds_threads_to_manifest_lock(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    out = tmp_path / "out"
    import time as time_module

    _write_lock(out, "manifest", acquired_at=time_module.time() - 10)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        [
            "cyl",
            "batch-download-for-predict",
            str(out),
            "--scan-ids-file",
            str(ids_file),
            "--lock-staleness-seconds",
            "5",
        ],
    )

    assert result.exit_code == 0, result.output
    assert sorted(_read_manifest(out)["scan_keys"]) == ["scan_1"]


def test_batch_cli_manifest_write_after_simulated_mid_batch_crash_is_healed_by_retry(
    tmp_path, monkeypatch
):
    """Confirms design.md's Risks/Trade-offs claim: a same-scan_ids retry closes the
    mid-batch-crash gap. Stages scans 1-3 directly (bypassing the manifest write, standing
    in for a process killed before it ran), then re-runs the real batch command over
    scan_ids 1-5 and confirms the manifest ends up with all five."""
    _patch_batch(monkeypatch)
    out = tmp_path / "out"
    client = auth.make_authed_client(None)
    for scan_id in (1, 2, 3):
        result = dfp.stage_one_scan(client, scan_id, out)
        assert result.status == "ok"
    assert not (out / RUN_MANIFEST_FILENAME).exists()

    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1, 2, 3, 4, 5]", encoding="utf-8")
    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert result.exit_code == 0, result.output
    assert sorted(_read_manifest(out)["scan_keys"]) == [
        "scan_1",
        "scan_2",
        "scan_3",
        "scan_4",
        "scan_5",
    ]


# --- Post-/review-pr hardening (found via /review-pr on #655, fixed same PR) ---------


def test_batch_cli_manifest_write_oserror_becomes_clean_error_not_raw_crash(tmp_path, monkeypatch):
    """PR #655 review finding: write_run_manifest only caught LockContendedError around the
    whole acquire+write — an OSError from the write itself (disk full, permissions) escaped
    as a raw, unhandled traceback instead of the click.ClickException every other
    manifest-write failure mode in this design produces.

    `atomic_write_bytes` is also used for per-frame writes (`download_frames_for_predict`),
    so the mock must only fail for the manifest's own write — a blanket failure would break
    frame staging first and never even reach the manifest write, exercising the wrong path."""
    _patch_batch(monkeypatch)
    real_atomic_write_bytes = dfp.atomic_write_bytes

    def _fail_only_for_manifest(path, data):
        if Path(path).name == RUN_MANIFEST_FILENAME:
            raise OSError("simulated disk-full mid-write")
        return real_atomic_write_bytes(path, data)

    monkeypatch.setattr(dfp, "atomic_write_bytes", _fail_only_for_manifest)

    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli, ["cyl", "batch-download-for-predict", str(out), "--scan-ids-file", str(ids_file)]
    )

    assert (out / "scan_1" / "scan_1.scan_metadata.json").exists(), (
        "the scan itself must have staged successfully — otherwise this test never "
        "reaches the manifest write at all"
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


def test_batch_cli_lock_staleness_seconds_zero_is_rejected(tmp_path, monkeypatch):
    """PR #655 review finding: --lock-staleness-seconds 0 (or negative) made `age <=
    staleness_seconds` false for essentially any lock, however freshly held, silently
    reclaiming a live lock and defeating the entire feature with no error."""
    _patch_batch(monkeypatch)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli,
        [
            "cyl",
            "batch-download-for-predict",
            str(out),
            "--scan-ids-file",
            str(ids_file),
            "--lock-staleness-seconds",
            "0",
        ],
    )

    assert result.exit_code != 0
    assert "lock-staleness-seconds" in result.output.lower()


def test_batch_cli_lock_staleness_seconds_negative_is_rejected(tmp_path, monkeypatch):
    _patch_batch(monkeypatch)
    ids_file = tmp_path / "scan_ids.json"
    ids_file.write_text("[1]", encoding="utf-8")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        cli,
        [
            "cyl",
            "batch-download-for-predict",
            str(out),
            "--scan-ids-file",
            str(ids_file),
            "--lock-staleness-seconds",
            "-5",
        ],
    )

    assert result.exit_code != 0
    assert "lock-staleness-seconds" in result.output.lower()
