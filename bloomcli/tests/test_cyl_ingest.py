"""bloomctl cyl ingest-result — envelope helpers + command wiring (mocked client)."""

import io
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from sleap_roots_contracts import RunManifest

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


@pytest.fixture(autouse=True)
def _clear_argo_workflow_name_env(monkeypatch):
    """Every test starts with ARGO_WORKFLOW_NAME unset, regardless of the
    ambient shell — deterministic for the many pre-existing tests that don't
    care about it, and every test that DOES care sets it explicitly via
    monkeypatch.setenv (auto-reverted)."""
    monkeypatch.delenv("ARGO_WORKFLOW_NAME", raising=False)


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


# --- fix-cyl-pipeline-run-scan-status: ARGO_WORKFLOW_NAME threading ---------


def test_call_insert_envelope_includes_argo_workflow_name_when_given():
    captured = {}

    class _RPC:
        def execute(self):
            return type("R", (), {"data": RESULT_OK})()

    class _Client:
        def rpc(self, name, params):
            captured["params"] = params
            return _RPC()

    ing.call_insert_envelope(_Client(), ENVELOPE, argo_workflow_name="wf-abc")
    assert captured["params"] == {"envelope": ENVELOPE, "p_argo_workflow_name": "wf-abc"}


def test_call_insert_envelope_omits_the_key_when_argo_workflow_name_is_none():
    captured = {}

    class _RPC:
        def execute(self):
            return type("R", (), {"data": RESULT_OK})()

    class _Client:
        def rpc(self, name, params):
            captured["params"] = params
            return _RPC()

    ing.call_insert_envelope(_Client(), ENVELOPE, argo_workflow_name=None)
    assert "p_argo_workflow_name" not in captured["params"]


def test_resolve_argo_workflow_name_reads_the_env_var(monkeypatch):
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "sleap-roots-pipeline-abc123")
    assert ing.resolve_argo_workflow_name() == "sleap-roots-pipeline-abc123"


def test_resolve_argo_workflow_name_returns_none_when_unset():
    assert ing.resolve_argo_workflow_name() is None


def test_resolve_argo_workflow_name_returns_none_when_empty(monkeypatch):
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "")
    assert ing.resolve_argo_workflow_name() is None


def test_ingest_one_envelope_threads_argo_workflow_name_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "wf-batch-1")
    captured = {}

    def cap(client, env, **kw):
        captured.update(kw)
        return RESULT_OK

    monkeypatch.setattr(ing, "call_insert_envelope", cap)
    envelope_path = tmp_path / "scan_1.result.json"
    envelope_path.write_text(json.dumps(ENVELOPE), encoding="utf-8")
    result = ing.ingest_one_envelope(object(), envelope_path)
    assert result.status == "ok"
    assert captured == {"argo_workflow_name": "wf-batch-1"}


def test_ingest_one_envelope_omits_argo_workflow_name_when_env_unset(tmp_path, monkeypatch):
    captured = {}

    def cap(client, env, **kw):
        captured.update(kw)
        return RESULT_OK

    monkeypatch.setattr(ing, "call_insert_envelope", cap)
    envelope_path = tmp_path / "scan_1.result.json"
    envelope_path.write_text(json.dumps(ENVELOPE), encoding="utf-8")
    ing.ingest_one_envelope(object(), envelope_path)
    assert captured == {"argo_workflow_name": None}


def _patch_authed(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())


def test_cli_happy_path(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code == 0, res.output
    assert "55" in res.output


def test_cli_sends_original_envelope_unchanged(monkeypatch):
    captured = {}

    def cap(client, env, **_kw):
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
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_NOOP)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code == 0, res.output
    assert "already ingested" in res.output.lower()


def test_cli_no_scan_is_actionable(monkeypatch):
    _patch_authed(monkeypatch)

    def boom(client, env, **_kw):
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

    def mark_rpc(client, env, **_kw):
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
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE), "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["source_id"] == 55
    assert out["was_noop"] is False


def test_cli_json_output_on_noop(monkeypatch):
    _patch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_NOOP)
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
        ing, "call_insert_envelope", lambda c, e, **_kw: (_ for _ in ()).throw(AssertionError("reached"))
    )
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code != 0
    assert "login" in res.output.lower()


def test_cli_permission_denied_names_role(monkeypatch):
    _patch_authed(monkeypatch)

    def boom(client, env, **_kw):
        raise _api_error("permission denied for function insert_cyl_result_envelope", code="42501")

    monkeypatch.setattr(ing, "call_insert_envelope", boom)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code != 0
    assert "bloom_writer" in res.output or "EXECUTE" in res.output


def test_cli_blobs_pass_through_unchanged(monkeypatch):
    captured = {}

    def cap(client, env, **_kw):
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
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
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
        ing,
        "call_insert_envelope",
        lambda c, e, **_kw: called.__setitem__("rpc", True) or RESULT_OK,
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
    monkeypatch.setattr(ing, "call_insert_envelope", lambda c, e, **_kw: source_only)
    env = json.loads(FIXTURE.read_text(encoding="utf-8"))
    env["traits"] = []
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", "-"], input=json.dumps(env))
    assert res.exit_code == 0, res.output
    assert "traits=0" in res.output
    assert "blobs=0" in res.output


def test_cli_unknown_rpc_error_surfaced_verbatim(monkeypatch):
    _patch_authed(monkeypatch)

    def boom(client, env, **_kw):
        raise _api_error("some brand new server error not in the match table")

    monkeypatch.setattr(ing, "call_insert_envelope", boom)
    res = CliRunner().invoke(cli, ["cyl", "ingest-result", str(FIXTURE)])
    assert res.exit_code != 0
    assert "some brand new server error not in the match table" in res.output


def test_cli_contract_version_mismatch_reports_both_versions(monkeypatch):
    _patch_authed(monkeypatch)

    def boom(client, env, **_kw):
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
    monkeypatch.setattr(ing, "call_insert_envelope", lambda c, e, **_kw: None)
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

    def cap(client, env, **_kw):
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

    def cap(client, env, **_kw):
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

    def mark_rpc(client, env, **_kw):
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

    def mark_rpc(client, env, **_kw):
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
        ing,
        "call_insert_envelope",
        lambda c, e, **_kw: called.__setitem__("rpc", True) or RESULT_OK,
    )
    res = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(FIXTURE), "--predictions-dir", str(tmp_path)],
    )
    assert res.exit_code != 0
    assert not called["rpc"]


# --- review follow-ups: PR #508 (bloom #407) -------------------------------


def test_cli_predictions_dir_missing_idempotency_key_fails_actionably(monkeypatch, tmp_path):
    """Regression: PredictionManifest's contract-level default ("") for
    idempotency_key means an envelope can validly omit it; --predictions-dir
    must fail fast with a readable message, not a raw KeyError/traceback."""
    called = {"rpc": False}
    monkeypatch.setattr(
        climod, "_authed_client", lambda p: called.__setitem__("auth", True) or object()
    )
    monkeypatch.setattr(
        ing,
        "call_insert_envelope",
        lambda c, e, **_kw: called.__setitem__("rpc", True) or RESULT_OK,
    )
    env = json.loads(FIXTURE.read_text(encoding="utf-8"))
    del env["provenance"]["idempotency_key"]
    res = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", "-", "--predictions-dir", str(PREDICTIONS_DIR)],
        input=json.dumps(env),
    )
    assert res.exit_code != 0
    assert not called["rpc"]
    assert "idempotency_key" in res.output
    # Must not be a raw traceback -- click.ClickException output starts with "Error:".
    assert "Traceback" not in res.output


def test_upload_pending_blobs_missing_root_type_key_is_recorded_not_raised():
    """A malformed PendingBlob (missing the 'root_type' key entirely) must be
    recorded as a failed outcome, not raise an uncaught KeyError that kills
    the whole batch -- the same 'one bad blob can't abort the batch'
    guarantee the docstring already promises for every other failure mode."""
    bad_blob = {
        "kind": "predictions_slp",
        "scan_key": SCAN_KEY,
        "checksum": "irrelevant",
        "file_size": 1,
        "s3_location": None,
        "box_link": None,
    }
    pending = [
        ing.PendingBlob(
            blob=bad_blob,
            local_path=PREDICTIONS_DIR / "scan0K9E8BI.modelrice-primary.rootprimary.slp",
        )
    ]
    client = _NotFoundClient()
    report = ing.upload_pending_blobs(
        client, pending, scan_key=SCAN_KEY, idempotency_key="idem123"
    )
    assert not report.all_ok
    assert len(report.failed) == 1
    assert "root_type" in report.failed[0].error


def test_upload_blob_reraises_non_404_storage_errors():
    """A non-404 StorageApiError (permission denied, timeout, 5xx) during the
    pre-upload existence check must propagate, not be silently reinterpreted
    as 'object doesn't exist' -- that would mask real infra/permission
    problems as ordinary first uploads."""
    from storage3.exceptions import StorageApiError

    class _ForbiddenBucket:
        def download(self, object_path):
            raise StorageApiError("permission denied", "403", 403)

        def upload(self, object_path, data):
            raise AssertionError("must not attempt upload after a non-404 existence-check error")

    client = type("C", (), {"storage": type("S", (), {"from_": lambda self, n: _ForbiddenBucket()})()})()
    with pytest.raises(StorageApiError):
        ing.upload_blob(
            client,
            PREDICTIONS_DIR / "scan0K9E8BI.modelrice-primary.rootprimary.slp",
            "some/path.slp",
            "irrelevant",
        )


def test_build_pending_blobs_rejects_path_traversal(tmp_path):
    """A manifest artifact whose slp_path escapes predictions_dir (e.g. a
    corrupted/malicious manifest pointing at ../../.. or an absolute path)
    must be rejected before any file is read or uploaded -- predict-produced
    manifests are trusted pipeline output today, but this is cheap
    defense-in-depth against a buggy or tampered manifest reaching a shared,
    multi-reader storage bucket."""
    manifest_dict = json.loads(
        (PREDICTIONS_DIR / "scan0K9E8BI.predictions.json").read_text()
    )
    manifest_dict["artifacts"][0]["slp_path"] = "../../../../etc/passwd"
    (tmp_path / "scan0K9E8BI.predictions.json").write_text(json.dumps(manifest_dict))
    manifest = ing.load_predictions_manifest(tmp_path, SCAN_KEY)
    with pytest.raises(ing.BlobConstructionError) as excinfo:
        ing.build_pending_blobs(manifest, tmp_path, existing_blobs=[])
    assert "slp_path" in str(excinfo.value) or "outside" in str(excinfo.value).lower()


def test_blob_object_path_rejects_path_separators_in_scan_key():
    with pytest.raises(ing.BlobConstructionError):
        ing.blob_object_path("scan/../evil", "idem123", "predictions_slp", "primary")


def test_blob_object_path_rejects_path_separators_in_idempotency_key():
    with pytest.raises(ing.BlobConstructionError):
        ing.blob_object_path("scan0K9E8BI", "idem/../evil", "predictions_slp", "primary")


# --- batch: pure helpers ------------------------------------------------------


def _envelope_for(scan_key):
    """A copy of the fixture envelope, re-keyed to `scan_key` (provenance + traits + idempotency)."""
    env = json.loads(FIXTURE.read_text(encoding="utf-8"))
    env["provenance"]["scan_key"] = scan_key
    env["provenance"]["idempotency_key"] = f"idem-{scan_key}"
    for t in env["traits"]:
        t["scan_key"] = scan_key
    return env


def _write_envelope(directory, scan_key):
    path = directory / f"{scan_key}.result.json"
    path.write_text(json.dumps(_envelope_for(scan_key)), encoding="utf-8")
    return path


def _write_run_manifest(directory, *, scan_keys, pipeline_run_id="wf-test"):
    """Write a valid run_manifest.json (bloom #678 — write-back's manifest-scoped discovery)."""
    manifest = RunManifest(pipeline_run_id=pipeline_run_id, scan_keys=scan_keys)
    (directory / ing.RUN_MANIFEST_FILENAME).write_text(manifest.model_dump_json(), encoding="utf-8")


def test_discover_envelopes_returns_sorted_paths(tmp_path):
    _write_envelope(tmp_path, "scan_b")
    _write_envelope(tmp_path, "scan_a")
    discovered = ing.discover_envelopes(tmp_path)
    assert [p.name for p in discovered.paths] == ["scan_a.result.json", "scan_b.result.json"]
    assert discovered.missing_scan_keys == []


def test_discover_envelopes_empty_dir_returns_empty_list(tmp_path):
    discovered = ing.discover_envelopes(tmp_path)
    assert discovered.paths == []
    assert discovered.missing_scan_keys == []


def test_discover_envelopes_missing_dir_raises(tmp_path):
    with pytest.raises(ing.EnvelopeError):
        ing.discover_envelopes(tmp_path / "nope")


def test_discover_envelopes_file_instead_of_dir_raises(tmp_path):
    f = tmp_path / "not_a_dir.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(ing.EnvelopeError):
        ing.discover_envelopes(f)


def test_discover_envelopes_is_non_recursive(tmp_path):
    _write_envelope(tmp_path, "scan_top")
    nested = tmp_path / "subdir"
    nested.mkdir()
    _write_envelope(nested, "scan_nested")
    discovered = ing.discover_envelopes(tmp_path)
    assert [p.name for p in discovered.paths] == ["scan_top.result.json"]


# --- batch: run_manifest.json-scoped discovery (bloom #678) ------------------


def test_discover_envelopes_scopes_to_run_manifest(tmp_path):
    _write_envelope(tmp_path, "scan_1")
    _write_envelope(tmp_path, "scan_2")
    _write_run_manifest(tmp_path, scan_keys=["scan_1"])

    discovered = ing.discover_envelopes(tmp_path)

    assert [p.name for p in discovered.paths] == ["scan_1.result.json"]


def test_discover_envelopes_no_run_manifest_is_fully_unscoped(tmp_path):
    _write_envelope(tmp_path, "scan_1")
    _write_envelope(tmp_path, "scan_2")

    discovered = ing.discover_envelopes(tmp_path)

    assert [p.name for p in discovered.paths] == ["scan_1.result.json", "scan_2.result.json"]
    assert discovered.missing_scan_keys == []


def test_discover_envelopes_missing_run_manifest_scan_key_is_reported(tmp_path):
    _write_envelope(tmp_path, "scan_1")
    _write_run_manifest(tmp_path, scan_keys=["scan_1", "scan_2"])

    discovered = ing.discover_envelopes(tmp_path)

    assert [p.name for p in discovered.paths] == ["scan_1.result.json"]
    assert discovered.missing_scan_keys == ["scan_2"]


def test_discover_envelopes_excluded_file_logs_debug(tmp_path, caplog):
    _write_envelope(tmp_path, "scan_1")
    _write_envelope(tmp_path, "scan_2")
    _write_run_manifest(tmp_path, scan_keys=["scan_1"])

    with caplog.at_level("DEBUG", logger="bloomctl.cyl.ingest"):
        ing.discover_envelopes(tmp_path)

    debug_records = [r for r in caplog.records if r.levelname == "DEBUG"]
    assert len(debug_records) == 1
    assert "scan_2" in debug_records[0].message


def test_discover_envelopes_no_exclusion_logs_no_debug_line(tmp_path, caplog):
    _write_envelope(tmp_path, "scan_1")
    _write_run_manifest(tmp_path, scan_keys=["scan_1"])

    with caplog.at_level("DEBUG", logger="bloomctl.cyl.ingest"):
        ing.discover_envelopes(tmp_path)

    assert [r for r in caplog.records if r.levelname == "DEBUG"] == []


def test_discover_envelopes_multiple_excluded_files_log_one_aggregated_line(tmp_path, caplog):
    _write_envelope(tmp_path, "scan_1")
    _write_envelope(tmp_path, "scan_2")
    _write_envelope(tmp_path, "scan_3")
    _write_run_manifest(tmp_path, scan_keys=["scan_1"])

    with caplog.at_level("DEBUG", logger="bloomctl.cyl.ingest"):
        ing.discover_envelopes(tmp_path)

    debug_records = [r for r in caplog.records if r.levelname == "DEBUG"]
    assert len(debug_records) == 1
    assert "scan_2" in debug_records[0].message
    assert "scan_3" in debug_records[0].message


def test_discover_envelopes_malformed_run_manifest_json_raises(tmp_path):
    _write_envelope(tmp_path, "scan_1")
    (tmp_path / ing.RUN_MANIFEST_FILENAME).write_text("{ not json", encoding="utf-8")

    with pytest.raises(ing.EnvelopeError):
        ing.discover_envelopes(tmp_path)


def test_discover_envelopes_run_manifest_wrong_schema_raises(tmp_path):
    _write_envelope(tmp_path, "scan_1")
    (tmp_path / ing.RUN_MANIFEST_FILENAME).write_text(
        json.dumps({"pipeline_run_id": "wf-test"}), encoding="utf-8"
    )

    with pytest.raises(ing.EnvelopeError):
        ing.discover_envelopes(tmp_path)


def test_discover_envelopes_unreadable_run_manifest_raises(tmp_path, monkeypatch):
    _write_envelope(tmp_path, "scan_1")
    _write_run_manifest(tmp_path, scan_keys=["scan_1"])

    def _boom(self, *args, **kwargs):
        raise OSError("simulated permission error")

    monkeypatch.setattr(Path, "read_text", _boom)

    with pytest.raises(ing.EnvelopeError):
        ing.discover_envelopes(tmp_path)


def test_discover_envelopes_run_manifest_as_directory_raises(tmp_path):
    _write_envelope(tmp_path, "scan_1")
    (tmp_path / ing.RUN_MANIFEST_FILENAME).mkdir()

    with pytest.raises(ing.EnvelopeError):
        ing.discover_envelopes(tmp_path)


def test_discover_envelopes_permission_error_reading_run_manifest_raises(tmp_path, monkeypatch):
    """Review finding: Path.exists()/.is_file() only swallow ENOENT-class errors, not
    EACCES — a pre-check built on those (the original implementation of this fix) would
    let a permission-denied stat escape as a raw, uncaught PermissionError instead of
    failing loud with a readable EnvelopeError. Fixed by reading the manifest directly
    (FileNotFoundError -> absent/unscoped, any other OSError -> EnvelopeError) instead of
    pre-checking with exists()/is_file(). This test pins the observable contract —
    PermissionError specifically, not just a generic OSError stand-in — regardless of
    which internal call raises it."""
    _write_envelope(tmp_path, "scan_1")
    _write_run_manifest(tmp_path, scan_keys=["scan_1"])

    def _boom(self, *args, **kwargs):
        raise PermissionError("simulated permission error")

    monkeypatch.setattr(Path, "read_text", _boom)

    with pytest.raises(ing.EnvelopeError):
        ing.discover_envelopes(tmp_path)


def test_ingest_one_envelope_malformed_json_file(tmp_path):
    path = tmp_path / "bad.result.json"
    path.write_text("{ not json", encoding="utf-8")
    result = ing.ingest_one_envelope(object(), path)
    assert result.status == "failed"
    assert result.scan_key == "bad"
    assert result.error


def test_ingest_one_envelope_fails_contract_validation(tmp_path):
    env = _envelope_for("scan_bad")
    del env["provenance"]["params"]
    path = tmp_path / "scan_bad.result.json"
    path.write_text(json.dumps(env), encoding="utf-8")
    result = ing.ingest_one_envelope(object(), path)
    assert result.status == "failed"
    assert result.scan_key == "scan_bad"


def _skip_contract_validation(monkeypatch):
    """`_envelope_for`'s re-keyed envelopes carry a hand-rolled idempotency_key that (correctly)
    fails sleap-roots-contracts' derived-value check — these tests are about batch mechanics, not
    contract validation (already covered by `test_ingest_one_envelope_fails_contract_validation`
    and the unmodified-fixture blob-upload tests below), so bypass it."""
    monkeypatch.setattr(ing, "validate_envelope", lambda data: None)


def test_ingest_one_envelope_success(monkeypatch, tmp_path):
    _skip_contract_validation(monkeypatch)
    path = _write_envelope(tmp_path, "scan_ok")
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    result = ing.ingest_one_envelope(object(), path)
    assert result.status == "ok"
    assert result.scan_key == "scan_ok"


def test_ingest_one_envelope_noop_is_skipped(monkeypatch, tmp_path):
    _skip_contract_validation(monkeypatch)
    path = _write_envelope(tmp_path, "scan_dup")
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_NOOP)
    result = ing.ingest_one_envelope(object(), path)
    assert result.status == "skipped"


def test_ingest_one_envelope_rpc_error_is_mapped(monkeypatch, tmp_path):
    _skip_contract_validation(monkeypatch)
    path = _write_envelope(tmp_path, "scan_err")

    def boom(client, env, **_kw):
        raise _api_error("unresolvable image_ids: matched 1 of 2 to a scan")

    monkeypatch.setattr(ing, "call_insert_envelope", boom)
    result = ing.ingest_one_envelope(object(), path)
    assert result.status == "failed"
    assert "cyl_images" in result.error


def test_ingest_one_envelope_isolates_unexpected_error(monkeypatch, tmp_path):
    """A non-APIError exception (e.g. a gotrue auth error or httpx timeout, not just the
    already-handled postgrest.APIError) must be isolated into a failed ScanResult, never
    raised — review finding: this was previously uncaught and would crash the whole batch."""
    _skip_contract_validation(monkeypatch)
    path = _write_envelope(tmp_path, "scan_timeout")

    def boom(client, env, **_kw):
        raise TimeoutError("simulated network timeout")

    monkeypatch.setattr(ing, "call_insert_envelope", boom)
    result = ing.ingest_one_envelope(object(), path)
    assert result.status == "failed"
    assert result.scan_key == "scan_timeout"
    assert "simulated network timeout" in result.error


def test_ingest_one_envelope_isolates_unreadable_file_error(tmp_path):
    """load_envelope's Path.read_text can raise UnicodeDecodeError (a ValueError, not
    OSError) on a truncated/corrupt file — e.g. one an OOM-killed producer pod left
    mid-write. Review finding: this propagated past ingest_one_envelope's isolation
    entirely, since the try/except around the load_envelope call only caught
    EnvelopeError, and the broad `except Exception` catch only wrapped the later
    blob/RPC block, not this one — a real file with invalid UTF-8 bytes reproduces it
    without any monkeypatching."""
    path = tmp_path / "scan_corrupt.result.json"
    path.write_bytes(b"\xff\xfe\x00bad-utf8")

    result = ing.ingest_one_envelope(object(), path)

    assert result.status == "failed"
    assert result.scan_key == "scan_corrupt"


def test_batch_ingest_cli_isolates_unreadable_file_among_several(monkeypatch, tmp_path):
    """The same corrupt-file failure, exercised through the full batch command: it must
    be isolated to its own ScanResult, not abort ingestion of the other envelopes or
    skip the end-of-batch reconciliation call."""
    _patch_batch_authed(monkeypatch)
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "wf-corrupt")
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    calls = []
    monkeypatch.setattr(
        ing, "reconcile_unresolved_scans", lambda client, name: calls.append(name) or 0
    )
    _write_envelope(tmp_path, "scan_1")
    (tmp_path / "scan_corrupt.result.json").write_bytes(b"\xff\xfe\x00bad-utf8")
    _write_envelope(tmp_path, "scan_3")

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code != 0
    payload = {entry["scan_key"]: entry for entry in json.loads(result.output)}
    assert payload["scan_1"]["status"] == "ok"
    assert payload["scan_corrupt"]["status"] == "failed"
    assert payload["scan_3"]["status"] == "ok"
    assert calls == ["wf-corrupt"], "reconciliation must still run despite the corrupt file"


def test_batch_ingest_cli_isolates_unexpected_network_error_among_several(monkeypatch, tmp_path):
    _patch_batch_authed(monkeypatch)

    def _flaky_call(client, env, **_kw):
        if env["provenance"]["scan_key"] == "scan_2":
            raise TimeoutError("simulated network timeout")
        return RESULT_OK

    monkeypatch.setattr(ing, "call_insert_envelope", _flaky_call)
    for key in ("scan_1", "scan_2", "scan_3"):
        _write_envelope(tmp_path, key)

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code != 0
    payload = {entry["scan_key"]: entry for entry in json.loads(result.output)}
    assert payload["scan_1"]["status"] == "ok"
    assert payload["scan_2"]["status"] == "failed"
    assert "simulated network timeout" in payload["scan_2"]["error"]
    assert payload["scan_3"]["status"] == "ok"


def test_ingest_one_envelope_sends_envelope_unchanged(monkeypatch, tmp_path):
    _skip_contract_validation(monkeypatch)
    captured = {}
    path = _write_envelope(tmp_path, "scan_ok")

    def cap(client, env, **_kw):
        captured["env"] = env
        return RESULT_OK

    monkeypatch.setattr(ing, "call_insert_envelope", cap)
    ing.ingest_one_envelope(object(), path)
    assert captured["env"]["provenance"]["scan_key"] == "scan_ok"


def test_ingest_one_envelope_predictions_dir_missing_idempotency_key(monkeypatch, tmp_path):
    _skip_contract_validation(monkeypatch)
    env = _envelope_for("scan_noidem")
    del env["provenance"]["idempotency_key"]
    path = tmp_path / "scan_noidem.result.json"
    path.write_text(json.dumps(env), encoding="utf-8")

    result = ing.ingest_one_envelope(object(), path, predictions_dir=tmp_path / "predictions")
    assert result.status == "failed"
    assert "idempotency_key" in result.error


def _nested_predictions_dir(base_dir, scan_key):
    """Copy the flat PREDICTIONS_DIR fixture into base_dir/{scan_key}/ (predict's own nested
    batch-output layout)."""
    import shutil

    nested = base_dir / scan_key
    nested.mkdir(parents=True)
    for f in PREDICTIONS_DIR.iterdir():
        shutil.copy(f, nested / f.name.replace(SCAN_KEY, scan_key))
    return base_dir


def test_ingest_one_envelope_predictions_dir_missing_manifest(monkeypatch, tmp_path):
    _skip_contract_validation(monkeypatch)
    path = _write_envelope(tmp_path, "scan_ok")
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)

    result = ing.ingest_one_envelope(object(), path, predictions_dir=tmp_path / "predictions")
    assert result.status == "failed"


@pytest.mark.parametrize(
    "malicious_scan_key",
    ["../../evil", "..\\..\\evil", "/etc/passwd", "a/../../b"],
)
def test_ingest_one_envelope_rejects_path_traversal_scan_key(
    monkeypatch, tmp_path, malicious_scan_key
):
    """Review finding: provenance.scan_key is producer-supplied JSON content with no
    path-safety constraint from sleap-roots-contracts. Using it as a directory segment
    (predictions_dir / scan_key) must be rejected before any local filesystem access, the
    same way blob_object_path already rejects it for the object-storage key."""
    _skip_contract_validation(monkeypatch)
    env = _envelope_for("scan_ok")
    env["provenance"]["scan_key"] = malicious_scan_key
    path = tmp_path / "scan_ok.result.json"
    path.write_text(json.dumps(env), encoding="utf-8")

    called = {"manifest": False}

    def _boom(*a, **k):
        called["manifest"] = True
        raise AssertionError("must not read any predictions manifest for an unsafe scan_key")

    monkeypatch.setattr(ing, "load_predictions_manifest", _boom)

    result = ing.ingest_one_envelope(
        object(), path, predictions_dir=tmp_path / "predictions"
    )
    assert result.status == "failed"
    assert not called["manifest"]
    assert "scan_key" in result.error


def test_ingest_one_envelope_predictions_dir_uploads_blobs(tmp_path, monkeypatch):
    """Unmodified fixture (real scan_key + real idempotency_key) — exercises the full,
    contract-validated happy path, not just batch mechanics."""
    captured = {}
    path = tmp_path / f"{SCAN_KEY}.result.json"
    path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    predictions_root = tmp_path / "predictions"
    _nested_predictions_dir(predictions_root, SCAN_KEY)

    def cap(client, env, **_kw):
        captured["env"] = env
        return RESULT_OK

    def fake_upload(client, pending, *, scan_key, idempotency_key):
        for p in pending:
            p.blob["s3_location"] = f"s3://x/{p.blob['root_type']}.slp"
        return ing.BlobUploadReport(
            [ing.BlobUploadOutcome(root_type=p.blob["root_type"], ok=True) for p in pending]
        )

    monkeypatch.setattr(ing, "call_insert_envelope", cap)
    monkeypatch.setattr(ing, "upload_pending_blobs", fake_upload)

    result = ing.ingest_one_envelope(object(), path, predictions_dir=predictions_root)
    assert result.status == "ok"
    assert len(captured["env"]["blobs"]) == 2


def test_ingest_one_envelope_predictions_dir_upload_failure(tmp_path, monkeypatch):
    called = {"rpc": False}
    path = tmp_path / f"{SCAN_KEY}.result.json"
    path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    predictions_root = tmp_path / "predictions"
    _nested_predictions_dir(predictions_root, SCAN_KEY)

    def mark_rpc(client, env, **_kw):
        called["rpc"] = True
        return RESULT_OK

    def failing_upload(client, pending, *, scan_key, idempotency_key):
        return ing.BlobUploadReport(
            [ing.BlobUploadOutcome(root_type="primary", ok=False, error="boom")]
        )

    monkeypatch.setattr(ing, "call_insert_envelope", mark_rpc)
    monkeypatch.setattr(ing, "upload_pending_blobs", failing_upload)

    result = ing.ingest_one_envelope(object(), path, predictions_dir=predictions_root)
    assert result.status == "failed"
    assert not called["rpc"]


# --- batch: command wiring -----------------------------------------------------


def _patch_batch_authed(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())
    _skip_contract_validation(monkeypatch)


def test_batch_ingest_cli_happy_path(monkeypatch, tmp_path):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    for key in ("scan_1", "scan_2", "scan_3"):
        _write_envelope(tmp_path, key)

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path)])

    assert result.exit_code == 0, result.output


# --- fix-cyl-pipeline-run-scan-status: batch reconciliation call -----------


def test_batch_ingest_cli_reconciles_after_all_envelopes_when_workflow_name_set(
    monkeypatch, tmp_path
):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "wf-batch-1")
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    calls = []
    monkeypatch.setattr(
        ing, "reconcile_unresolved_scans", lambda client, name: calls.append(name) or 0
    )
    for key in ("scan_1", "scan_2"):
        _write_envelope(tmp_path, key)

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert calls == ["wf-batch-1"], "must be called exactly once, after every envelope"


def test_batch_ingest_cli_no_reconcile_call_when_workflow_name_unset(monkeypatch, tmp_path):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)

    def boom(client, name):
        raise AssertionError("must not be called when ARGO_WORKFLOW_NAME is unset")

    monkeypatch.setattr(ing, "reconcile_unresolved_scans", boom)
    _write_envelope(tmp_path, "scan_1")

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path)])

    assert result.exit_code == 0, result.output


def test_batch_ingest_cli_reconciles_even_with_zero_envelopes(monkeypatch, tmp_path):
    """The reconciliation call must fire even when there is nothing to ingest —
    every scan under this workflow name failed prediction before producing any
    file at all. Must not be gated on `if discovered.paths: ...`."""
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "wf-empty")
    called = {"auth": False}
    monkeypatch.setattr(
        climod, "_authed_client", lambda p: called.__setitem__("auth", True) or object()
    )
    calls = []
    monkeypatch.setattr(
        ing, "reconcile_unresolved_scans", lambda client, name: calls.append(name) or 0
    )
    # tmp_path is empty: no envelope files, no run_manifest.json

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert called["auth"] is True
    assert calls == ["wf-empty"]


def test_batch_ingest_result_missing_scan_key_alone_still_reconciles_when_workflow_name_set(
    monkeypatch, tmp_path
):
    """Only manifest-declared-missing entries, no files at all — the existing
    'never authenticate' behavior (see the sibling _makes_no_auth_call test)
    is for when ARGO_WORKFLOW_NAME is unset; when it IS set, a client is still
    needed purely to make the one reconciliation call."""
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "wf-missing-only")
    called = {"auth": False}
    monkeypatch.setattr(
        climod, "_authed_client", lambda p: called.__setitem__("auth", True) or object()
    )
    calls = []
    monkeypatch.setattr(
        ing, "reconcile_unresolved_scans", lambda client, name: calls.append(name) or 0
    )
    _write_run_manifest(tmp_path, scan_keys=["scan_1", "scan_2"])

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code != 0  # both declared scan_keys are still reported failed
    assert called["auth"] is True
    assert calls == ["wf-missing-only"]


def test_batch_ingest_cli_reconcile_failure_does_not_crash_and_is_reported(monkeypatch, tmp_path):
    """Review finding: the reconciliation call had no exception handling of its own, unlike
    every other RPC call in this file — a transient failure on this one closing call, after
    every real envelope already ingested successfully, crashed the whole command with an
    unhandled traceback instead of the batch's own summary."""
    _patch_batch_authed(monkeypatch)
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "wf-reconcile-boom")
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)

    def boom(client, name):
        raise _api_error("simulated transient reconciliation failure")

    monkeypatch.setattr(ing, "reconcile_unresolved_scans", boom)
    for key in ("scan_1", "scan_2"):
        _write_envelope(tmp_path, key)

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    # A clean click ctx.exit(1) surfaces as result.exception == SystemExit(1); an unhandled
    # crash (the pre-fix behavior) surfaces as the raw exception itself with no parseable JSON
    # on stdout — assert on the latter, not on `exception is None`, which SystemExit fails too.
    assert isinstance(result.exception, SystemExit), (
        f"must exit cleanly via click, not crash with a raw exception: {result.exception!r}"
    )
    assert result.exit_code != 0
    payload = {entry["scan_key"]: entry for entry in json.loads(result.output)}
    assert payload["scan_1"]["status"] == "ok"
    assert payload["scan_2"]["status"] == "ok"
    reconciliation_entries = [e for e in payload.values() if e["status"] == "failed"]
    assert len(reconciliation_entries) == 1
    assert "simulated transient reconciliation failure" in reconciliation_entries[0]["error"]


def test_batch_ingest_cli_reconcile_permission_error_does_not_name_the_wrong_rpc(
    monkeypatch, tmp_path
):
    """Round 2 /review-pr finding: map_rpc_error's 'permission denied' branch is
    hardcoded to insert_cyl_result_envelope's own grant (bloom_writer/bloom_admin) — but
    the reconciliation call is against fail_cyl_pipeline_run_scans_without_result, which
    is granted to bloom_workflows only, a different role entirely. Reusing that mapper
    here would tell an operator to log in with the wrong account for the wrong RPC."""
    _patch_batch_authed(monkeypatch)
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "wf-perm-denied")
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)

    def boom(client, name):
        raise _api_error("permission denied for function fail_cyl_pipeline_run_scans_without_result")

    monkeypatch.setattr(ing, "reconcile_unresolved_scans", boom)
    _write_envelope(tmp_path, "scan_1")

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    payload = {entry["scan_key"]: entry for entry in json.loads(result.output)}
    reconciliation_entries = [e for e in payload.values() if e["status"] == "failed"]
    assert len(reconciliation_entries) == 1
    error = reconciliation_entries[0]["error"]
    assert "insert_cyl_result_envelope" not in error, (
        "must not name the wrong RPC in the hint"
    )
    assert "bloom_writer" not in error and "bloom_admin" not in error, (
        "must not suggest the wrong role — fail_cyl_pipeline_run_scans_without_result "
        "is granted to bloom_workflows only"
    )


def test_batch_ingest_cli_reconcile_failure_on_empty_batch_is_reported(monkeypatch, tmp_path):
    """Same isolation, exercised through the zero-envelopes early-return branch, which has its
    own bespoke 'nothing to ingest' message/exit path distinct from the main batch flow."""
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "wf-empty-boom")
    monkeypatch.setattr(
        climod, "_authed_client", lambda p: object()
    )

    def boom(client, name):
        raise _api_error("simulated transient reconciliation failure")

    monkeypatch.setattr(ing, "reconcile_unresolved_scans", boom)
    # tmp_path is empty: no envelope files, no run_manifest.json

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert isinstance(result.exception, SystemExit), (
        f"must exit cleanly via click, not crash with a raw exception: {result.exception!r}"
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["status"] == "failed"
    assert "simulated transient reconciliation failure" in payload[0]["error"]


def test_batch_ingest_cli_reconcile_success_logs_the_reconciled_count(
    monkeypatch, tmp_path, caplog
):
    """The reconciliation call's return value (how many scans it closed out) was previously
    discarded silently — review finding: no CLI-visible signal that N scans were just marked
    failed by this batch."""
    _patch_batch_authed(monkeypatch)
    monkeypatch.setenv("ARGO_WORKFLOW_NAME", "wf-logs-count")
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    monkeypatch.setattr(ing, "reconcile_unresolved_scans", lambda client, name: 3)
    _write_envelope(tmp_path, "scan_1")

    with caplog.at_level("INFO", logger="bloomctl.cyl.ingest"):
        result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path)])

    assert result.exit_code == 0, result.output
    info_records = [r for r in caplog.records if r.levelname == "INFO"]
    assert any("3" in r.message and "wf-logs-count" in r.message for r in info_records)


def test_batch_ingest_cli_json_all_ok(monkeypatch, tmp_path):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    for key in ("scan_1", "scan_2"):
        _write_envelope(tmp_path, key)

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 2
    assert all(entry["status"] == "ok" for entry in payload)


def test_batch_ingest_cli_mixed_statuses_json_output(monkeypatch, tmp_path):
    """Review finding: this scenario (a batch with all three statuses at once — the normal
    case in a real run, not an edge case) had no test on the ingest side, asymmetric with the
    download side's equivalent test."""
    _patch_batch_authed(monkeypatch)

    def _selective_call(client, env, **_kw):
        scan_key = env["provenance"]["scan_key"]
        if scan_key == "scan_2":
            return RESULT_NOOP
        if scan_key == "scan_3":
            raise _api_error("unresolvable image_ids: matched 1 of 2 to a scan")
        return RESULT_OK

    monkeypatch.setattr(ing, "call_insert_envelope", _selective_call)
    for key in ("scan_1", "scan_2", "scan_3"):
        _write_envelope(tmp_path, key)

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code != 0
    payload = {entry["scan_key"]: entry for entry in json.loads(result.output)}
    assert payload["scan_1"]["status"] == "ok"
    assert payload["scan_2"]["status"] == "skipped"
    assert payload["scan_3"]["status"] == "failed"
    assert "cyl_images" in payload["scan_3"]["error"]


def test_batch_ingest_cli_mixed_statuses_default_output(monkeypatch, tmp_path):
    _patch_batch_authed(monkeypatch)

    def _selective_call(client, env, **_kw):
        scan_key = env["provenance"]["scan_key"]
        if scan_key == "scan_2":
            return RESULT_NOOP
        if scan_key == "scan_3":
            raise _api_error("unresolvable image_ids: matched 1 of 2 to a scan")
        return RESULT_OK

    monkeypatch.setattr(ing, "call_insert_envelope", _selective_call)
    for key in ("scan_1", "scan_2", "scan_3"):
        _write_envelope(tmp_path, key)

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path)])

    assert result.exit_code != 0
    assert "1 skipped" in result.output.lower()
    assert "1 failed" in result.output.lower()
    assert "scan_3" in result.output


def test_batch_ingest_cli_isolates_one_bad_envelope(monkeypatch, tmp_path):
    """Always runs (mocked, no importorskip) — the core isolation guarantee."""
    _patch_batch_authed(monkeypatch)

    def selective_call(client, env, **_kw):
        if env["provenance"]["scan_key"] == "scan_bad":
            raise _api_error("invalid envelope: missing provenance.inputs object")
        return RESULT_OK

    monkeypatch.setattr(ing, "call_insert_envelope", selective_call)

    _write_envelope(tmp_path, "scan_1")
    _write_envelope(tmp_path, "scan_3")
    bad_env = _envelope_for("scan_bad")
    del bad_env["provenance"]["params"]
    (tmp_path / "scan_bad.result.json").write_text(json.dumps(bad_env), encoding="utf-8")

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path)])

    assert result.exit_code != 0
    assert "scan_bad" in result.output


def test_batch_ingest_oracle_matches_extract_batch_output_shape(tmp_path, monkeypatch):
    """Manual, dev-machine only — self-skips in CI (verifies discover_envelopes' flat-glob
    assumption against the real extract_batch output shape)."""
    pytest.importorskip("trait_extractor")
    from trait_extractor.extractor import _SIDECAR_SUFFIX  # noqa: F401

    # extract_batch's own output_dir is flat: {scan_key}.result.json directly, no nesting —
    # discover_envelopes' non-recursive glob must match that, not a nested layout.
    _write_envelope(tmp_path, "scan_1")
    discovered = ing.discover_envelopes(tmp_path)
    assert len(discovered.paths) == 1
    assert discovered.paths[0].parent == tmp_path


def test_batch_ingest_cli_malformed_envelope_file_is_isolated(monkeypatch, tmp_path):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    _write_envelope(tmp_path, "scan_1")
    _write_envelope(tmp_path, "scan_3")
    (tmp_path / "scan_bad.result.json").write_text("{ not json", encoding="utf-8")

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path)])

    assert result.exit_code != 0
    assert "scan_bad" in result.output


def test_batch_ingest_cli_empty_dir_is_noop(monkeypatch, tmp_path):
    called = {"auth": False}
    monkeypatch.setattr(
        climod, "_authed_client", lambda p: called.__setitem__("auth", True) or object()
    )

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert not called["auth"]


def test_batch_ingest_cli_nonexistent_dir_makes_no_call(monkeypatch, tmp_path):
    called = {"auth": False}
    monkeypatch.setattr(
        climod, "_authed_client", lambda p: called.__setitem__("auth", True) or object()
    )

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path / "nope")])

    assert result.exit_code != 0
    assert not called["auth"]


# --- batch: run_manifest.json-scoped discovery wiring (bloom #678) -----------


def test_batch_ingest_result_missing_run_manifest_scan_key_is_reported_failed_json(
    monkeypatch, tmp_path
):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    _write_envelope(tmp_path, "scan_1")
    _write_run_manifest(tmp_path, scan_keys=["scan_1", "scan_9"])

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code != 0
    payload = {entry["scan_key"]: entry for entry in json.loads(result.output)}
    assert payload["scan_1"]["status"] == "ok"
    assert payload["scan_9"]["status"] == "failed"
    assert "scan_9" in payload["scan_9"]["error"]


def test_batch_ingest_result_missing_run_manifest_scan_key_is_reported_failed_default_output(
    monkeypatch, tmp_path
):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    _write_envelope(tmp_path, "scan_1")
    _write_run_manifest(tmp_path, scan_keys=["scan_1", "scan_9"])

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path)])

    assert result.exit_code != 0
    assert "scan_9" in result.output


def test_batch_ingest_cli_malformed_run_manifest_makes_no_auth_call(monkeypatch, tmp_path):
    called = {"auth": False}
    monkeypatch.setattr(
        climod, "_authed_client", lambda p: called.__setitem__("auth", True) or object()
    )
    _write_envelope(tmp_path, "scan_1")
    (tmp_path / ing.RUN_MANIFEST_FILENAME).write_text("{ not json", encoding="utf-8")

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path)])

    assert result.exit_code != 0
    assert not called["auth"]


def test_batch_ingest_result_missing_scan_key_alone_makes_no_auth_call(monkeypatch, tmp_path):
    called = {"auth": False}
    monkeypatch.setattr(
        climod, "_authed_client", lambda p: called.__setitem__("auth", True) or object()
    )
    _write_run_manifest(tmp_path, scan_keys=["scan_1", "scan_2"])

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code != 0
    assert not called["auth"]
    payload = {entry["scan_key"]: entry for entry in json.loads(result.output)}
    assert payload["scan_1"]["status"] == "failed"
    assert payload["scan_2"]["status"] == "failed"


def test_batch_ingest_result_mixed_present_and_missing_scan_keys(monkeypatch, tmp_path):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    _write_envelope(tmp_path, "scan_1")
    _write_run_manifest(tmp_path, scan_keys=["scan_1", "scan_2"])

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code != 0
    payload = {entry["scan_key"]: entry for entry in json.loads(result.output)}
    assert payload["scan_1"]["status"] == "ok"
    assert payload["scan_2"]["status"] == "failed"


def test_batch_ingest_cli_run_manifest_present_all_scan_keys_ingest_successfully(
    monkeypatch, tmp_path
):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    _write_envelope(tmp_path, "scan_1")
    _write_envelope(tmp_path, "scan_2")
    _write_run_manifest(tmp_path, scan_keys=["scan_1", "scan_2"])

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 2
    assert all(entry["status"] == "ok" for entry in payload)


def test_batch_ingest_result_body_scan_key_mismatch_resolves_to_single_entry(
    monkeypatch, tmp_path
):
    """Review finding: discover_envelopes' missing_scan_keys is filename-derived, while
    ingest_one_envelope relabels by the envelope body's own provenance.scan_key. Without
    reconciliation, the same scan_key could appear twice in one batch with contradictory
    ok/failed statuses, silently shadowing a real successful write-back."""
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    # File is named scan_A.result.json, but its own body claims scan_key = scan_B.
    mismatched = _envelope_for("scan_B")
    (tmp_path / "scan_A.result.json").write_text(json.dumps(mismatched), encoding="utf-8")
    _write_run_manifest(tmp_path, scan_keys=["scan_A", "scan_B"])

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload) == 1
    assert payload[0]["scan_key"] == "scan_B"
    assert payload[0]["status"] == "ok"


def test_batch_ingest_result_collision_drop_logs_debug(monkeypatch, tmp_path, caplog):
    """The exclusion path (out-of-scope files) logs at debug level; the collision-drop
    path (a resolved filename/body mismatch) should too, for the same operator-trail
    reason — otherwise the dropped filename disappears from the record entirely."""
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    mismatched = _envelope_for("scan_B")
    (tmp_path / "scan_A.result.json").write_text(json.dumps(mismatched), encoding="utf-8")
    _write_run_manifest(tmp_path, scan_keys=["scan_A", "scan_B"])

    with caplog.at_level("DEBUG", logger="bloomctl.cyl.ingest"):
        result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    debug_records = [r for r in caplog.records if r.levelname == "DEBUG"]
    assert len(debug_records) == 1
    assert "scan_B" in debug_records[0].message


def test_batch_ingest_result_mismatch_resolved_alongside_a_genuinely_missing_key(
    monkeypatch, tmp_path
):
    """A resolved filename/body mismatch (scan_C, via scan_A.result.json) coexisting with
    a separate, genuinely-missing manifest key (scan_D, no file at all) in the same batch
    — the reconciliation must only drop the collided entry, not the unrelated one."""
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    mismatched = _envelope_for("scan_C")
    (tmp_path / "scan_A.result.json").write_text(json.dumps(mismatched), encoding="utf-8")
    _write_run_manifest(tmp_path, scan_keys=["scan_A", "scan_C", "scan_D"])

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code != 0
    payload = {entry["scan_key"]: entry for entry in json.loads(result.output)}
    assert payload["scan_C"]["status"] == "ok"
    assert payload["scan_D"]["status"] == "failed"
    assert len(payload) == 2


def test_batch_ingest_cli_noop_reported_as_skipped(monkeypatch, tmp_path):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_NOOP)
    _write_envelope(tmp_path, "scan_1")

    result = CliRunner().invoke(cli, ["cyl", "batch-ingest-result", str(tmp_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload[0]["status"] == "skipped"


def test_batch_ingest_cli_predictions_dir_uploads_blobs(monkeypatch, tmp_path):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)

    def fake_upload(client, pending, *, scan_key, idempotency_key):
        for p in pending:
            p.blob["s3_location"] = f"s3://x/{p.blob['root_type']}.slp"
        return ing.BlobUploadReport(
            [ing.BlobUploadOutcome(root_type=p.blob["root_type"], ok=True) for p in pending]
        )

    monkeypatch.setattr(ing, "upload_pending_blobs", fake_upload)

    envelopes_dir = tmp_path / "envelopes"
    envelopes_dir.mkdir()
    _write_envelope(envelopes_dir, SCAN_KEY)
    predictions_root = tmp_path / "predictions"
    _nested_predictions_dir(predictions_root, SCAN_KEY)

    result = CliRunner().invoke(
        cli,
        [
            "cyl",
            "batch-ingest-result",
            str(envelopes_dir),
            "--predictions-dir",
            str(predictions_root),
        ],
    )

    assert result.exit_code == 0, result.output


def test_batch_ingest_cli_predictions_dir_missing_manifest_isolates_one(monkeypatch, tmp_path):
    _patch_batch_authed(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)

    envelopes_dir = tmp_path / "envelopes"
    envelopes_dir.mkdir()
    _write_envelope(envelopes_dir, "scan_1")
    _write_envelope(envelopes_dir, SCAN_KEY)
    predictions_root = tmp_path / "predictions"
    predictions_root.mkdir()
    # Only SCAN_KEY has a predictions manifest; "scan_1" doesn't.
    _nested_predictions_dir(predictions_root, SCAN_KEY)

    result = CliRunner().invoke(
        cli,
        [
            "cyl",
            "batch-ingest-result",
            str(envelopes_dir),
            "--predictions-dir",
            str(predictions_root),
        ],
    )

    assert result.exit_code != 0
    assert "scan_1" in result.output


def test_batch_ingest_cli_profile_option_passed_through(monkeypatch, tmp_path):
    captured = {}

    def fake_authed_client(profile):
        captured["profile"] = profile
        return object()

    monkeypatch.setattr(climod, "_authed_client", fake_authed_client)
    _skip_contract_validation(monkeypatch)
    monkeypatch.setattr(ing, "call_insert_envelope", lambda client, env, **_kw: RESULT_OK)
    _write_envelope(tmp_path, "scan_1")

    result = CliRunner().invoke(
        cli, ["cyl", "batch-ingest-result", str(tmp_path), "-p", "staging"]
    )

    assert result.exit_code == 0, result.output
    assert captured["profile"] == "staging"


def test_batch_ingest_cli_registration_shows_in_help():
    result = CliRunner().invoke(cli, ["cyl", "--help"])
    assert "batch-ingest-result" in result.output
