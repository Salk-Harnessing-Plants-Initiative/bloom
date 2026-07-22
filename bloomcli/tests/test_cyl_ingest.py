"""bloomctl cyl ingest-result — envelope helpers + command wiring (mocked client)."""

import io
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

import bloomctl.cli as climod
import bloomctl.cyl.ingest as ing
from bloomctl.cli import cli

FIXTURE = Path(__file__).parent / "fixtures" / "scan0K9E8BI.result.json"
ENVELOPE = json.loads(FIXTURE.read_text(encoding="utf-8"))
REPO_ROOT = Path(__file__).resolve().parents[2]

PREDICTIONS_DIR = Path(__file__).parent / "fixtures" / "predictions_scan0K9E8BI"
SCAN_KEY = "scan0K9E8BI"

RESULT_OK = {"source_id": 55, "scan_id": 7, "trait_count": 2, "blob_count": 0, "was_noop": False}
# The RPC returns a null scan_id on a no-op re-delivery (cyl-trait-writeback).
RESULT_NOOP = {
    "source_id": 55,
    "scan_id": None,
    "trait_count": 0,
    "blob_count": 0,
    "was_noop": True,
}

# The exact RAISE EXCEPTION substrings from the RPC migration
# (supabase/migrations/20260706170000_cyl_writeback_contract_a3.sql), with the
# `%` placeholders interpolated as Postgres would deliver them.
RPC_ERRORS = [
    "invalid envelope: expected a JSON object",
    "invalid envelope: missing provenance object",
    "invalid envelope: missing provenance.inputs object",
    "invalid envelope: traits must be an array",
    "invalid envelope: blobs must be an array",
    "contract_version mismatch: got 0.0.0, pinned 0.1.0a3 (single leading v ignored)",
    "empty or absent idempotency_key",
    "invalid envelope: missing provenance.scan_key",
    "trait scan_key disagrees with provenance.scan_key",
    "blob scan_key disagrees with provenance.scan_key",
    "no image_ids: cannot resolve a scan",
    "non-numeric image_id in inputs.image_ids",
    "unresolvable image_ids: matched 1 of 2 to a scan",
    "image_ids resolve to 2 scans, expected exactly 1",
    "non-scan-grain trait rejected (grain=image)",
    "invalid trait: missing name",
    "invalid blob: file_size must be an integer, got foo",
]


def _api_error(message, code="P0001"):
    """A genuine postgrest.APIError, as raised by client.rpc(...).execute()."""
    from postgrest import APIError

    return APIError({"message": message, "code": code, "details": None, "hint": None})


# --- 3.x pure helpers -------------------------------------------------------


def test_load_envelope_from_path():
    data = ing.load_envelope(str(FIXTURE))
    assert data["provenance"]["scan_key"] == "scan0K9E8BI"


def test_load_envelope_from_stdin():
    data = ing.load_envelope("-", stdin=io.StringIO(FIXTURE.read_text(encoding="utf-8")))
    assert data == ENVELOPE


def test_load_envelope_missing_file(tmp_path):
    with pytest.raises(ing.EnvelopeError):
        ing.load_envelope(str(tmp_path / "nope.json"))


def test_load_envelope_invalid_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ing.EnvelopeError):
        ing.load_envelope(str(bad))


def test_load_envelope_empty_stdin():
    with pytest.raises(ing.EnvelopeError):
        ing.load_envelope("-", stdin=io.StringIO("   \n"))


def test_validate_accepts_fixture():
    ing.validate_envelope(ENVELOPE)  # must not raise


def test_validate_rejects_malformed():
    with pytest.raises(ing.EnvelopeValidationError) as excinfo:
        ing.validate_envelope({"provenance": {}, "traits": []})
    assert "valid" in str(excinfo.value).lower()


def test_validate_error_is_not_raw_pydantic():
    # The command surfaces its own error type, not pydantic's ValidationError.
    from pydantic import ValidationError

    with pytest.raises(ing.EnvelopeValidationError) as excinfo:
        ing.validate_envelope({"traits": []})
    assert not isinstance(excinfo.value, ValidationError)


def test_summarize_ingested_names_source_and_counts():
    s = ing.summarize_result(RESULT_OK)
    assert "55" in s
    assert "already" not in s.lower()


def test_summarize_source_only_zero_counts():
    s = ing.summarize_result(
        {"source_id": 9, "scan_id": 7, "trait_count": 0, "blob_count": 0, "was_noop": False}
    )
    assert "0" in s
    assert "already" not in s.lower()


def test_summarize_noop_is_benign_and_tolerates_null_scan():
    s = ing.summarize_result(RESULT_NOOP)  # scan_id is None
    assert "already ingested" in s.lower()
    assert "55" in s


@pytest.mark.parametrize("msg", RPC_ERRORS)
def test_map_rpc_error_covers_every_rpc_string(msg):
    out = ing.map_rpc_error(msg, profile="prod")
    assert out  # non-empty
    assert msg in out  # original message preserved (never swallowed)


def test_map_rpc_error_scan_resolution_is_actionable():
    out = ing.map_rpc_error("unresolvable image_ids: matched 1 of 2 to a scan", profile="staging")
    assert "cyl_images" in out
    assert "staging" in out


def test_map_rpc_error_non_numeric_is_scan_resolution():
    out = ing.map_rpc_error("non-numeric image_id in inputs.image_ids", profile="prod")
    assert "cyl_images" in out


def test_map_rpc_error_unknown_passes_through_verbatim():
    assert ing.map_rpc_error("something totally unknown") == "something totally unknown"


def test_map_rpc_error_none_does_not_crash():
    out = ing.map_rpc_error(None)
    assert out  # some non-empty fallback


# --- 4.x command wiring -----------------------------------------------------


def test_call_insert_envelope_builds_rpc_call():
    captured = {}

    class _RPC:
        def execute(self):
            return type("R", (), {"data": RESULT_OK})()

    class _Client:
        def rpc(self, name, params):
            captured["name"] = name
            captured["params"] = params
            return _RPC()

    out = ing.call_insert_envelope(_Client(), ENVELOPE)
    assert out == RESULT_OK
    assert captured["name"] == "insert_cyl_result_envelope"
    assert captured["params"] == {"envelope": ENVELOPE}


def _patch_authed(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())


def test_cli_happy_path(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env: RESULT_OK)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code == 0, res.output
    assert "55" in res.output


def test_cli_sends_original_envelope_unchanged(monkeypatch):
    captured = {}

    def cap(client, env):
        captured["env"] = env
        return RESULT_OK

    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", cap)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code == 0, res.output
    assert captured["env"] == ENVELOPE
    assert (
        captured["env"]["provenance"]["idempotency_key"]
        == ENVELOPE["provenance"]["idempotency_key"]
    )


def test_cli_noop_is_not_an_error(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env: RESULT_NOOP)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code == 0, res.output
    assert "already ingested" in res.output.lower()


def test_cli_no_scan_is_actionable(monkeypatch):
    _patch_authed(monkeypatch)

    def boom(client, env):
        raise _api_error("unresolvable image_ids: matched 1 of 2 to a scan")

    monkeypatch.setattr(ing, "call_insert_envelope", boom)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE), "-p", "staging"])
    assert res.exit_code != 0
    assert "cyl_images" in res.output


def test_cli_validation_fails_before_auth_or_call(monkeypatch):
    called = {"auth": False, "rpc": False}

    def mark_auth(profile):
        called["auth"] = True
        return object()

    def mark_rpc(client, env):
        called["rpc"] = True
        return RESULT_OK

    monkeypatch.setattr(climod, "_authed_client", mark_auth)
    monkeypatch.setattr(ing, "call_insert_envelope", mark_rpc)
    # Model-required but RPC-ignored field removed -> gate must reject before any I/O.
    bad = json.loads(FIXTURE.read_text(encoding="utf-8"))
    del bad["provenance"]["params"]
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", "-"], input=json.dumps(bad))
    assert res.exit_code != 0
    assert not called["auth"]
    assert not called["rpc"]


def test_cli_bad_json_makes_no_call(monkeypatch, tmp_path):
    called = {"auth": False}
    monkeypatch.setattr(
        climod, "_authed_client", lambda p: called.__setitem__("auth", True) or object()
    )
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(bad)])
    assert res.exit_code != 0
    assert not called["auth"]


def test_cli_json_output(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env: RESULT_OK)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE), "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["source_id"] == 55
    assert out["was_noop"] is False


def test_cli_json_output_on_noop(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env: RESULT_NOOP)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE), "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["was_noop"] is True
    assert out["source_id"] == 55


def test_cli_missing_credentials_hints_login(monkeypatch, tmp_path):
    import bloomctl.credentials as creds

    monkeypatch.setattr(creds, "default_config_dir", lambda: tmp_path / ".bloom")
    # Would raise if reached — proves creds fail before the RPC call.
    monkeypatch.setattr(
        ing, "call_insert_envelope", lambda c, e: (_ for _ in ()).throw(AssertionError("reached"))
    )
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code != 0
    assert "login" in res.output.lower()


def test_cli_permission_denied_names_role(monkeypatch):
    _patch_authed(monkeypatch)

    def boom(client, env):
        raise _api_error("permission denied for function insert_cyl_result_envelope", code="42501")

    monkeypatch.setattr(ing, "call_insert_envelope", boom)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code != 0
    assert "bloom_writer" in res.output or "EXECUTE" in res.output


def test_cli_blobs_pass_through_unchanged(monkeypatch):
    captured = {}

    def cap(client, env):
        captured["env"] = env
        return RESULT_OK

    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", cap)
    env = json.loads(FIXTURE.read_text(encoding="utf-8"))
    env["blobs"] = [
        {
            "kind": "predictions_slp",
            "root_type": "primary",
            "scan_key": "scan0K9E8BI",
            "s3_location": "s3://bucket/scan0K9E8BI.primary.slp",
        }
    ]
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", "-"], input=json.dumps(env))
    assert res.exit_code == 0, res.output
    assert captured["env"]["blobs"] == env["blobs"]


def test_cli_stdin_end_to_end(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env: RESULT_OK)
    res = CliRunner().invoke(
        cli, ["cyl", "ingest-result", "-"], input=FIXTURE.read_text(encoding="utf-8")
    )
    assert res.exit_code == 0, res.output
    assert "55" in res.output


def test_cli_registration_in_help():
    res = CliRunner().invoke(cli, ["--help"])
    assert "cyl" in res.output
    sub = CliRunner().invoke(cli, ["cyl", "--help"])
    assert "ingest-result" in sub.output
    # download is grouped by data type under `cyl`, not at the top level.
    assert "download" in sub.output
    assert "download" not in cli.commands


# --- review follow-ups: spec-scenario gaps + robustness guards ---------------


@pytest.mark.parametrize("payload", ["[1, 2, 3]", '"just a string"', "42", "null"])
def test_load_envelope_rejects_non_object_json(payload):
    with pytest.raises(ing.EnvelopeError) as excinfo:
        ing.load_envelope("-", stdin=io.StringIO(payload))
    assert "object" in str(excinfo.value).lower()


def test_cli_non_object_json_makes_no_call(monkeypatch):
    called = {"auth": False, "rpc": False}
    monkeypatch.setattr(
        climod, "_authed_client", lambda p: called.__setitem__("auth", True) or object()
    )
    monkeypatch.setattr(
        ing, "call_insert_envelope", lambda c, e: called.__setitem__("rpc", True) or RESULT_OK
    )
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", "-"], input="[1, 2, 3]")
    assert res.exit_code != 0
    assert not called["auth"]
    assert not called["rpc"]


def test_cli_source_only_envelope_reports_zero_counts(monkeypatch):
    _patch_authed(monkeypatch)
    source_only = {
        "source_id": 9,
        "scan_id": 7,
        "trait_count": 0,
        "blob_count": 0,
        "was_noop": False,
    }
    monkeypatch.setattr(ing, "call_insert_envelope", lambda c, e: source_only)
    env = json.loads(FIXTURE.read_text(encoding="utf-8"))
    env["traits"] = []
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", "-"], input=json.dumps(env))
    assert res.exit_code == 0, res.output
    assert "traits=0" in res.output
    assert "blobs=0" in res.output


def test_cli_unknown_rpc_error_surfaced_verbatim(monkeypatch):
    _patch_authed(monkeypatch)

    def boom(client, env):
        raise _api_error("some brand new server error not in the match table")

    monkeypatch.setattr(ing, "call_insert_envelope", boom)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code != 0
    assert "some brand new server error not in the match table" in res.output


def test_cli_contract_version_mismatch_reports_both_versions(monkeypatch):
    _patch_authed(monkeypatch)

    def boom(client, env):
        raise _api_error(
            "contract_version mismatch: got 0.0.0, pinned 0.1.0a3 (single leading v ignored)"
        )

    monkeypatch.setattr(ing, "call_insert_envelope", boom)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code != 0
    assert "0.0.0" in res.output
    assert "0.1.0a3" in res.output


def test_cli_non_dict_rpc_response_errors(monkeypatch):
    # If the RPC ever returns a non-object, fail cleanly (not a bare AttributeError).
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda c, e: None)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code != 0
    assert "unexpected rpc response" in res.output.lower()


def _current_migration_sql():
    """Read the current cyl write-back migration (highest timestamp wins)."""
    migrations = sorted((REPO_ROOT / "supabase" / "migrations").glob("*cyl_writeback*.sql"))
    if not migrations:
        pytest.skip("cyl write-back migration not found from this checkout")
    return migrations[-1].read_text(encoding="utf-8")


# The exact substrings map_rpc_error keys on to append an actionable hint. If the
# RPC reworded one of these, the hint would silently stop firing (the message
# still surfaces verbatim, but the actionable guidance is lost) — this test fails
# instead, forcing the two to be re-synced.
_MAP_RPC_ERROR_MARKERS = [
    "no image_ids",
    "unresolvable image_ids",
    "image_ids resolve to",
    "non-numeric image_id",
    "contract_version mismatch",
    "empty or absent idempotency_key",
    "disagrees with provenance.scan_key",
    "missing provenance.scan_key",
    "permission denied",
]


def test_map_rpc_error_markers_still_present_in_migration():
    sql = _current_migration_sql()
    # "permission denied" is a Postgres system error, not a RAISE in the migration.
    missing = [m for m in _MAP_RPC_ERROR_MARKERS if m != "permission denied" and m not in sql]
    assert not missing, f"map_rpc_error markers have drifted from the RPC migration: {missing}"


def test_map_rpc_error_raise_strings_are_never_swallowed():
    """Every literal RAISE EXCEPTION message in the migration round-trips verbatim."""
    for raw in re.findall(r"RAISE EXCEPTION\s+'([^']+)'", _current_migration_sql()):
        msg = raw.replace("%", "X")  # interpolate placeholders as Postgres would
        assert msg in ing.map_rpc_error(msg), f"RPC message swallowed: {raw!r}"


# --- 2.x manifest reading + BlobRef construction (bloom #407) ---------------


def test_load_predictions_manifest_reads_fixture():
    manifest = ing.load_predictions_manifest(PREDICTIONS_DIR, SCAN_KEY)
    assert manifest.scan_key == SCAN_KEY
    assert len(manifest.artifacts) == 2
    root_types = {a.root_type for a in manifest.artifacts}
    assert root_types == {"primary", "crown"}


def test_load_predictions_manifest_missing_file(tmp_path):
    with pytest.raises(ing.BlobConstructionError) as excinfo:
        ing.load_predictions_manifest(tmp_path, "no-such-scan")
    assert "no-such-scan.predictions.json" in str(excinfo.value)


def test_load_predictions_manifest_malformed_json(tmp_path):
    bad = tmp_path / "badscan.predictions.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ing.BlobConstructionError):
        ing.load_predictions_manifest(tmp_path, "badscan")


def test_load_predictions_manifest_fails_schema_validation(tmp_path):
    bad = tmp_path / "badscan.predictions.json"
    bad.write_text(json.dumps({"scan_key": "badscan", "artifacts": [{"root_type": "bogus"}]}))
    with pytest.raises(ing.BlobConstructionError):
        ing.load_predictions_manifest(tmp_path, "badscan")


def test_build_pending_blobs_from_manifest():
    manifest = ing.load_predictions_manifest(PREDICTIONS_DIR, SCAN_KEY)
    pending = ing.build_pending_blobs(manifest, PREDICTIONS_DIR, existing_blobs=[])
    assert len(pending) == 2
    by_root = {p.blob["root_type"]: p for p in pending}
    primary = by_root["primary"]
    assert primary.blob["kind"] == "predictions_slp"
    assert primary.blob["scan_key"] == SCAN_KEY
    assert primary.blob["checksum"] == (
        "032e90ea6effacbc3542381fecc1d72b09390077b1edb2ff0fabc26f7eed0044"
    )
    assert primary.blob["file_size"] == 32
    assert primary.blob["s3_location"] is None
    assert primary.blob["box_link"] is None
    assert primary.local_path == PREDICTIONS_DIR / "scan0K9E8BI.modelrice-primary.rootprimary.slp"


def test_build_pending_blobs_rejects_conflicting_existing_blob():
    manifest = ing.load_predictions_manifest(PREDICTIONS_DIR, SCAN_KEY)
    existing = [{"root_type": "primary", "scan_key": SCAN_KEY, "s3_location": "s3://already/there.slp"}]
    with pytest.raises(ing.BlobConstructionError) as excinfo:
        ing.build_pending_blobs(manifest, PREDICTIONS_DIR, existing_blobs=existing)
    assert "primary" in str(excinfo.value)
    assert SCAN_KEY in str(excinfo.value)


# --- 3.x checksum verification (bloom #407) ---------------------------------


def test_verify_blob_checksum_matches(tmp_path):
    p = tmp_path / "a.slp"
    p.write_bytes(b"hello")
    import hashlib

    ing.verify_blob_checksum(p, hashlib.sha256(b"hello").hexdigest())  # must not raise


def test_verify_blob_checksum_mismatch_names_both(tmp_path):
    p = tmp_path / "a.slp"
    p.write_bytes(b"hello")
    with pytest.raises(ing.BlobConstructionError) as excinfo:
        ing.verify_blob_checksum(p, "deadbeef")
    msg = str(excinfo.value)
    assert "deadbeef" in msg
    assert str(p) in msg


def test_verify_blob_checksum_missing_file(tmp_path):
    p = tmp_path / "does-not-exist.slp"
    with pytest.raises(ing.BlobConstructionError) as excinfo:
        ing.verify_blob_checksum(p, "deadbeef")
    assert str(p) in str(excinfo.value)


# --- 4.x upload + idempotency (bloom #407) ----------------------------------


def test_blob_object_path_is_a_plain_string_join():
    path = ing.blob_object_path("scan0K9E8BI", "idem123", "predictions_slp", "primary")
    assert path == "scan0K9E8BI/idem123/predictions_slp.primary.slp"
    assert "\\" not in path  # must never be a pathlib.Path (Windows backslash risk)


class _NotFoundBucket:
    """No object exists yet at any path."""

    def __init__(self):
        self.uploaded = {}

    def download(self, object_path):
        from storage3.exceptions import StorageApiError

        raise StorageApiError("Object not found", "404", 404)

    def upload(self, object_path, data):
        self.uploaded[object_path] = data


class _NotFoundStorage:
    def __init__(self, bucket):
        self.bucket = bucket

    def from_(self, name):
        assert name == "cyl-intermediates"
        return self.bucket


class _NotFoundClient:
    def __init__(self):
        self.bucket = _NotFoundBucket()
        self.storage = _NotFoundStorage(self.bucket)


def test_upload_blob_first_upload():
    client = _NotFoundClient()
    location, skipped = ing.upload_blob(client, PREDICTIONS_DIR / "scan0K9E8BI.modelrice-primary.rootprimary.slp", "some/path.slp", "032e90ea6effacbc3542381fecc1d72b09390077b1edb2ff0fabc26f7eed0044")
    assert skipped is False
    assert location == "some/path.slp"
    assert client.bucket.uploaded["some/path.slp"] == (
        PREDICTIONS_DIR / "scan0K9E8BI.modelrice-primary.rootprimary.slp"
    ).read_bytes()


class _ExistingBucket:
    """An object already exists at `object_path` with `existing_bytes`."""

    def __init__(self, object_path, existing_bytes):
        self.object_path = object_path
        self.existing_bytes = existing_bytes
        self.upload_called = False

    def download(self, object_path):
        if object_path == self.object_path:
            return self.existing_bytes
        from storage3.exceptions import StorageApiError

        raise StorageApiError("Object not found", "404", 404)

    def upload(self, object_path, data):
        self.upload_called = True


def test_upload_blob_skips_when_existing_checksum_matches():
    data = b"already uploaded bytes"
    checksum = __import__("hashlib").sha256(data).hexdigest()
    bucket = _ExistingBucket("some/path.slp", data)
    client = type("C", (), {"storage": type("S", (), {"from_": lambda self, n: bucket})()})()
    # local file content doesn't matter for a skip -- only the checksum comparison does
    location, skipped = ing.upload_blob(client, PREDICTIONS_DIR / "scan0K9E8BI.modelrice-primary.rootprimary.slp", "some/path.slp", checksum)
    assert skipped is True
    assert location == "some/path.slp"
    assert bucket.upload_called is False


def test_upload_blob_raises_on_path_collision():
    bucket = _ExistingBucket("some/path.slp", b"different existing bytes")
    client = type("C", (), {"storage": type("S", (), {"from_": lambda self, n: bucket})()})()
    with pytest.raises(ing.BlobConstructionError) as excinfo:
        ing.upload_blob(client, PREDICTIONS_DIR / "scan0K9E8BI.modelrice-primary.rootprimary.slp", "some/path.slp", "expectedchecksum")
    assert "some/path.slp" in str(excinfo.value)
    assert bucket.upload_called is False


def test_upload_pending_blobs_all_succeed():
    manifest = ing.load_predictions_manifest(PREDICTIONS_DIR, SCAN_KEY)
    pending = ing.build_pending_blobs(manifest, PREDICTIONS_DIR, existing_blobs=[])
    client = _NotFoundClient()
    report = ing.upload_pending_blobs(
        client, pending, scan_key=SCAN_KEY, idempotency_key="idem123"
    )
    assert report.all_ok
    assert len(report.outcomes) == 2
    assert not report.failed
    for outcome, p in zip(report.outcomes, pending):
        assert outcome.ok
        assert not outcome.skipped
        assert p.blob["s3_location"] == outcome.location


def test_upload_pending_blobs_one_failure_does_not_abort_the_batch():
    manifest = ing.load_predictions_manifest(PREDICTIONS_DIR, SCAN_KEY)
    pending = ing.build_pending_blobs(manifest, PREDICTIONS_DIR, existing_blobs=[])
    # corrupt the primary artifact's expected checksum so its verify step fails
    for p in pending:
        if p.blob["root_type"] == "primary":
            p.blob["checksum"] = "deliberately-wrong-checksum"
    client = _NotFoundClient()
    report = ing.upload_pending_blobs(
        client, pending, scan_key=SCAN_KEY, idempotency_key="idem123"
    )
    assert not report.all_ok
    assert len(report.failed) == 1
    assert report.failed[0].root_type == "primary"
    # the other (crown) blob still got uploaded -- one bad blob doesn't abort the batch
    crown_outcome = next(o for o in report.outcomes if o.root_type == "crown")
    assert crown_outcome.ok


# --- 5.x wire --predictions-dir into the command (bloom #407) ---------------


def test_cli_predictions_dir_omitted_pass_through_unchanged(monkeypatch):
    """Regression guard: omitting --predictions-dir must not change existing
    behavior at all (spec: 'No predictions-dir, envelope carrying blobs')."""
    captured = {}

    def cap(client, env):
        captured["env"] = env
        return RESULT_OK

    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", cap)
    env = json.loads(FIXTURE.read_text(encoding="utf-8"))
    env["blobs"] = [
        {
            "kind": "predictions_slp",
            "root_type": "primary",
            "scan_key": "scan0K9E8BI",
            "s3_location": "s3://bucket/scan0K9E8BI.primary.slp",
        }
    ]
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", "-"], input=json.dumps(env))
    assert res.exit_code == 0, res.output
    assert captured["env"]["blobs"] == env["blobs"]


def test_cli_predictions_dir_constructs_and_uploads_blobs(monkeypatch):
    captured = {}

    def cap(client, env):
        captured["env"] = env
        return RESULT_OK

    def fake_upload(client, pending, *, scan_key, idempotency_key):
        for p in pending:
            p.blob["s3_location"] = f"s3://x/{p.blob['root_type']}.slp"
        return ing.BlobUploadReport(
            [ing.BlobUploadOutcome(root_type=p.blob["root_type"], ok=True) for p in pending]
        )

    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", cap)
    monkeypatch.setattr(ing, "upload_pending_blobs", fake_upload)
    res = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(FIXTURE), "--predictions-dir", str(PREDICTIONS_DIR)],
    )
    assert res.exit_code == 0, res.output
    assert len(captured["env"]["blobs"]) == 2
    root_types = {b["root_type"] for b in captured["env"]["blobs"]}
    assert root_types == {"primary", "crown"}
    for b in captured["env"]["blobs"]:
        assert b["s3_location"] == f"s3://x/{b['root_type']}.slp"


def test_cli_predictions_dir_upload_failure_makes_no_rpc_call(monkeypatch):
    called = {"rpc": False}

    def mark_rpc(client, env):
        called["rpc"] = True
        return RESULT_OK

    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", mark_rpc)

    def failing_upload(client, pending, *, scan_key, idempotency_key):
        return ing.BlobUploadReport(
            [
                ing.BlobUploadOutcome(root_type="primary", ok=False, error="boom"),
                ing.BlobUploadOutcome(root_type="crown", ok=True),
            ]
        )

    monkeypatch.setattr(ing, "upload_pending_blobs", failing_upload)
    res = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(FIXTURE), "--predictions-dir", str(PREDICTIONS_DIR)],
    )
    assert res.exit_code != 0
    assert not called["rpc"]
    assert "primary" in res.output
    assert "boom" in res.output


def test_cli_predictions_dir_conflicting_blob_makes_no_upload_or_rpc_call(monkeypatch):
    called = {"upload": False, "rpc": False}

    def mark_upload(client, pending, *, scan_key, idempotency_key):
        called["upload"] = True
        return ing.BlobUploadReport([])

    def mark_rpc(client, env):
        called["rpc"] = True
        return RESULT_OK

    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "upload_pending_blobs", mark_upload)
    monkeypatch.setattr(ing, "call_insert_envelope", mark_rpc)

    env = json.loads(FIXTURE.read_text(encoding="utf-8"))
    env["blobs"] = [
        {"kind": "predictions_slp", "root_type": "primary", "scan_key": "scan0K9E8BI",
         "s3_location": "s3://already/there.slp"}
    ]
    res = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", "-", "--predictions-dir", str(PREDICTIONS_DIR)],
        input=json.dumps(env),
    )
    assert res.exit_code != 0
    assert not called["upload"]
    assert not called["rpc"]


def test_cli_predictions_dir_missing_manifest_makes_no_call(monkeypatch, tmp_path):
    called = {"auth": False, "rpc": False}
    monkeypatch.setattr(
        climod, "_authed_client", lambda p: called.__setitem__("auth", True) or object()
    )
    monkeypatch.setattr(
        ing, "call_insert_envelope", lambda c, e: called.__setitem__("rpc", True) or RESULT_OK
    )
    res = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(FIXTURE), "--predictions-dir", str(tmp_path)],
    )
    assert res.exit_code != 0
    assert not called["rpc"]
