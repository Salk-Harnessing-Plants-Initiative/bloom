"""Unit tests for bloom_mcp.supabase_client.

Env validation is lazy (deferred to first access / validate_env), so the
module imports with no Supabase env. These tests cover the prefix-application
logic, the basename validation, and the lazy env-validation contract against a
mocked supabase.Client — the network/Storage I/O itself is covered by the
staging smoke test.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# bloom_mcp is an installable package under bloommcp/src; inject that dir so the
# test imports it without an editable install (mirrors test_workflow_scaffolding).
_BLOOMMCP_DIR = Path(__file__).resolve().parents[2] / "bloommcp" / "src"
if str(_BLOOMMCP_DIR) not in sys.path:
    sys.path.insert(0, str(_BLOOMMCP_DIR))

from bloom_mcp import supabase_client  # noqa: E402


@pytest.fixture(autouse=True)
def _supabase_env(monkeypatch):
    """Set placeholder Supabase env for accessor tests.

    Validation is now lazy (per-call), so accessors raise without env. The
    lazy-validation tests below delenv via their own monkeypatch to assert the
    missing-var behavior.
    """
    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000")
    monkeypatch.setenv("BLOOM_AGENT_KEY", "fake-agent-jwt")


# ─── Helper: build a Supabase-client mock whose .storage.from_().download
#     and .upload return deterministic values. The fixture also yields a
#     handle to the inner `storage_from` mock so each test can assert on
#     the calls. ──────────────────────────────────────────────────────────
@pytest.fixture
def supabase_mock(monkeypatch):
    """Patch `supabase.create_client` to return a mock whose storage API
    returns canned payloads."""
    csv_text = b"col_a,col_b\n1,2\n3,4\n"

    storage_from = MagicMock()
    storage_from.download.return_value = csv_text
    storage_from.upload.return_value = {"path": "ok"}

    storage = MagicMock()
    storage.from_.return_value = storage_from

    client = MagicMock()
    client.storage = storage

    monkeypatch.setattr(
        supabase_client.supabase, "create_client", lambda url, key: client
    )
    return storage_from


# ───────────────────────── prefix logic ─────────────────────────

def test_read_input_csv_uses_input_prefix(supabase_mock):
    df = supabase_client.read_input_csv("accessions.csv")
    # Returned the parsed CSV from the canned bytes.
    assert list(df.columns) == ["col_a", "col_b"]
    assert df.shape == (2, 2)
    # Asked for the right bucket + prefix.
    supabase_mock.download.assert_called_once_with("bloommcp_input/accessions.csv")


# ─────────────────────── basename validation ────────────────────────

@pytest.mark.parametrize(
    "bad_name",
    ["nested/file.csv", "/leading.csv", "trailing/", ""],
)
def test_read_input_csv_rejects_non_basename(bad_name, supabase_mock):
    with pytest.raises(ValueError):
        supabase_client.read_input_csv(bad_name)
    # No network call when validation rejects.
    supabase_mock.download.assert_not_called()


# ─────────────────────── client construction ────────────────────────

def test_get_postgrest_client_returns_a_fresh_client(supabase_mock, monkeypatch):
    """Each call returns a new client — verifies no module-level cache."""
    calls = []

    def fake_create_client(url, key):
        c = MagicMock(name=f"client-{len(calls)}")
        calls.append(c)
        return c

    monkeypatch.setattr(supabase_client.supabase, "create_client", fake_create_client)

    c1 = supabase_client.get_postgrest_client()
    c2 = supabase_client.get_postgrest_client()
    assert c1 is not c2
    assert len(calls) == 2


# ─────────────────────── env-validation at import ────────────────────

def test_validate_env_raises_naming_supabase_url(monkeypatch):
    """Validation is lazy: validate_env() raises naming only the missing URL."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("BLOOM_AGENT_KEY", "fake")
    with pytest.raises(RuntimeError, match="SUPABASE_URL") as exc:
        supabase_client.validate_env()
    assert "BLOOM_AGENT_KEY" not in str(exc.value)


def test_validate_env_raises_naming_bloom_agent_key(monkeypatch):
    """validate_env() raises naming only the missing BLOOM_AGENT_KEY."""
    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000")
    monkeypatch.delenv("BLOOM_AGENT_KEY", raising=False)
    with pytest.raises(RuntimeError, match="BLOOM_AGENT_KEY") as exc:
        supabase_client.validate_env()
    assert "SUPABASE_URL" not in str(exc.value)


def test_module_imports_with_no_supabase_env(monkeypatch):
    """The module imports (reloads) cleanly with neither var set — no raise."""
    import importlib

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("BLOOM_AGENT_KEY", raising=False)
    importlib.reload(supabase_client)  # must not raise


# ═══════════════════════════════════════════════════════════════════════════
# Storage helpers (Task 2.1 of migrate-bloommcp-storage-to-supabase)
# ═══════════════════════════════════════════════════════════════════════════


def test_get_storage_client_returns_bucket_bound_handle(supabase_mock, monkeypatch):
    """`get_storage_client()` pre-binds to the `bloommcp-data` bucket."""
    captured: dict[str, str] = {}

    def fake_create_client(url, key):
        c = MagicMock()

        def from_(bucket):
            captured["bucket"] = bucket
            return supabase_mock

        c.storage.from_ = from_
        return c

    monkeypatch.setattr(supabase_client.supabase, "create_client", fake_create_client)

    handle = supabase_client.get_storage_client()
    assert handle is supabase_mock
    assert captured["bucket"] == "bloommcp-data"


# ─── list_prefix ────────────────────────────────────────────────────────────

def test_list_prefix_returns_basenames(supabase_mock):
    supabase_mock.list.return_value = [
        {"name": "v1_2026-06-05_initial", "id": "a"},
        {"name": "v2_2026-06-05_rerun", "id": "b"},
        {"name": "manifest.json", "id": "c"},
    ]
    names = supabase_client.list_prefix("bloommcp_output/qc_my_exp/")
    assert names == ["v1_2026-06-05_initial", "v2_2026-06-05_rerun", "manifest.json"]
    supabase_mock.list.assert_called_once_with("bloommcp_output/qc_my_exp/")


def test_list_prefix_returns_empty_when_no_objects(supabase_mock):
    """Absence is a normal state (e.g. AnalysisDir on a fresh experiment)."""
    supabase_mock.list.return_value = []
    assert supabase_client.list_prefix("bloommcp_output/qc_unknown/") == []


# ─── read_json / write_json ─────────────────────────────────────────────────

def test_read_json_parses_downloaded_bytes(supabase_mock):
    supabase_mock.download.return_value = b'{"manifest_schema_version": 1, "versions": []}'
    payload = supabase_client.read_json("bloommcp_output/qc_my_exp/manifest.json")
    assert payload == {"manifest_schema_version": 1, "versions": []}
    supabase_mock.download.assert_called_once_with("bloommcp_output/qc_my_exp/manifest.json")


def test_write_json_uploads_canonical_bytes_with_upsert(supabase_mock):
    """sort_keys + indent=2 means two semantically equal manifests serialize
    to the same bytes — important for sha256 stability."""
    payload = {"b": 2, "a": 1, "nested": {"y": True, "x": False}}
    supabase_client.write_json("bloommcp_output/qc_my_exp/manifest.json", payload)

    supabase_mock.upload.assert_called_once()
    kwargs = supabase_mock.upload.call_args.kwargs
    assert kwargs["path"] == "bloommcp_output/qc_my_exp/manifest.json"
    assert kwargs["file_options"]["upsert"] == "true"
    assert kwargs["file_options"]["content-type"] == "application/json"

    # Bytes are sort_keys=True + indent=2 — deterministic.
    body = kwargs["file"]
    import json as _json
    assert _json.loads(body) == payload
    assert body == _json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


# ─── upload_file / download_file ────────────────────────────────────────────

def test_upload_file_reads_bytes_and_calls_upload(supabase_mock, tmp_path):
    local = tmp_path / "v1_2026-06-05" / "_cleaned.csv"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"col_a,col_b\n1,2\n")

    supabase_client.upload_file(
        "bloommcp_output/qc_my_exp/v1_2026-06-05/_cleaned.csv", local
    )

    supabase_mock.upload.assert_called_once()
    kwargs = supabase_mock.upload.call_args.kwargs
    assert kwargs["path"] == "bloommcp_output/qc_my_exp/v1_2026-06-05/_cleaned.csv"
    assert kwargs["file"] == b"col_a,col_b\n1,2\n"
    assert kwargs["file_options"]["content-type"] == "text/csv"
    assert kwargs["file_options"]["upsert"] == "true"


@pytest.mark.parametrize(
    "suffix,expected_ct",
    [
        (".csv", "text/csv"),
        (".json", "application/json"),
        (".png", "image/png"),
        (".jpeg", "image/jpeg"),
        (".bin", "application/octet-stream"),
        ("", "application/octet-stream"),
    ],
)
def test_upload_file_infers_content_type_from_extension(
    supabase_mock, tmp_path, suffix, expected_ct
):
    local = tmp_path / f"out{suffix}"
    local.write_bytes(b"x")
    supabase_client.upload_file(f"bloommcp_output/x/v1/out{suffix}", local)
    kwargs = supabase_mock.upload.call_args.kwargs
    assert kwargs["file_options"]["content-type"] == expected_ct


def test_download_file_writes_to_local_path_creating_parents(supabase_mock, tmp_path):
    supabase_mock.download.return_value = b'{"hello": "world"}'
    local = tmp_path / "nested" / "deeper" / "file.json"
    assert not local.parent.exists()

    supabase_client.download_file("bloommcp_output/x/v1/file.json", local)

    assert local.read_bytes() == b'{"hello": "world"}'
    assert local.parent.is_dir()
    supabase_mock.download.assert_called_once_with("bloommcp_output/x/v1/file.json")
