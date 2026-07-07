"""bloomctl cyl ingest-result — envelope helpers + command wiring (mocked client)."""

import io
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import bloomctl.cli as climod
import bloomctl.ingest as ing
from bloomctl.cli import cli

FIXTURE = Path(__file__).parent / "fixtures" / "scan0K9E8BI.result.json"
ENVELOPE = json.loads(FIXTURE.read_text(encoding="utf-8"))

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
