"""Unit tests for the live-persistence smoke driver's pure logic.

The driver itself only runs end-to-end with the dev stack up, but its decision
logic — provenance assertions, the hash-compare loop, version-advance detection,
and the per-check summary/exit aggregation — is factored into pure helpers that
run with no live Supabase. We exercise them here, backing the download callable
with the in-memory ``fake_supabase_storage`` fixture so the hash loop runs against
the same storage boundary the real driver uses.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

# Load the driver by path — `scripts/` is not an importable package (no __init__).
_DRIVER_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "live_persistence_smoke.py"
)
_spec = importlib.util.spec_from_file_location("live_persistence_smoke", _DRIVER_PATH)
assert _spec and _spec.loader
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


# --- summarize / exit-code aggregation ----------------------------------------
def test_summarize_all_pass_exits_zero():
    checks = [smoke.Check("a", True), smoke.Check("b", True)]
    text, code = smoke.summarize(checks)
    assert code == 0
    assert "SMOKE PASSED" in text
    assert "FAIL" not in text


def test_summarize_any_fail_exits_one_and_names_check():
    checks = [smoke.Check("a", True), smoke.Check("bad-check", False, "boom")]
    text, code = smoke.summarize(checks)
    assert code == 1
    assert "SMOKE FAILED" in text
    assert "bad-check" in text  # the failing check is named
    assert "boom" in text


# --- provenance checks --------------------------------------------------------
def _good_prov_kwargs():
    return dict(
        schema_version=3,
        seed=42,
        agent="bloom_agent",
        environment="bloommcp==0.1.0+uvlock:abc",
        output_keys={"labels.csv": "bloommcp_output/clustering_turface/v1/labels.csv"},
        output_sha256={"labels.csv": "deadbeef"},
    )


def test_provenance_checks_all_pass_on_valid_v3_entry():
    checks = smoke.provenance_checks(**_good_prov_kwargs())
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_provenance_checks_flags_null_seed():
    kwargs = _good_prov_kwargs()
    kwargs["seed"] = None
    checks = smoke.provenance_checks(**kwargs)
    failed = [c.name for c in checks if not c.ok]
    assert any("seed non-null" in n for n in failed)
    assert any("seed == 42" in n for n in failed)


def test_provenance_checks_flags_v2_schema():
    kwargs = _good_prov_kwargs()
    kwargs["schema_version"] = 2
    checks = smoke.provenance_checks(**kwargs)
    assert any(c.name == "manifest schema == 3" and not c.ok for c in checks)


def test_provenance_checks_flags_wrong_agent_and_empty_environment():
    kwargs = _good_prov_kwargs()
    kwargs["agent"] = "someone_else"
    kwargs["environment"] = ""
    checks = smoke.provenance_checks(**kwargs)
    failed = {c.name for c in checks if not c.ok}
    assert any(n.startswith("agent ==") for n in failed)
    assert "environment present" in failed


def test_provenance_checks_flags_keyset_mismatch():
    kwargs = _good_prov_kwargs()
    kwargs["output_sha256"] = {"other.csv": "deadbeef"}
    checks = smoke.provenance_checks(**kwargs)
    assert any(
        c.name == "output_keys / output_sha256 share one key-set" and not c.ok
        for c in checks
    )


# --- hash-compare loop (against the fake storage boundary) --------------------
def test_hash_checks_pass_when_bytes_match(fake_supabase_storage):
    store = fake_supabase_storage
    key = "bloommcp_output/clustering_turface/v1/labels.csv"
    payload = b"sample_index,cluster\n0,1\n1,0\n"
    store.objects[key] = payload
    output_keys = {"labels.csv": key}
    output_sha256 = {"labels.csv": hashlib.sha256(payload).hexdigest()}

    checks = smoke.hash_checks(
        output_keys, output_sha256, read_bytes=lambda k: store.objects[k]
    )
    assert len(checks) == 1
    assert checks[0].ok


def test_hash_checks_fail_on_mismatch(fake_supabase_storage):
    store = fake_supabase_storage
    key = "bloommcp_output/clustering_turface/v1/labels.csv"
    store.objects[key] = b"actual bytes"
    output_keys = {"labels.csv": key}
    output_sha256 = {"labels.csv": hashlib.sha256(b"different").hexdigest()}

    checks = smoke.hash_checks(
        output_keys, output_sha256, read_bytes=lambda k: store.objects[k]
    )
    assert len(checks) == 1
    assert not checks[0].ok
    assert "recorded=" in checks[0].detail and "actual=" in checks[0].detail


def test_hash_checks_fail_on_download_error():
    def boom(_key):
        raise RuntimeError("object not found")

    checks = smoke.hash_checks(
        {"labels.csv": "missing/key"}, {"labels.csv": "abc"}, read_bytes=boom
    )
    assert len(checks) == 1
    assert not checks[0].ok
    assert "download failed" in checks[0].detail


# --- version-advance detection ------------------------------------------------
def test_version_advance_pass_v1_to_v2():
    assert smoke.version_advance_check("v1", "v2").ok


def test_version_advance_fail_when_not_advanced():
    assert not smoke.version_advance_check("v1", "v1").ok


# --- bounded retry ------------------------------------------------------------
def test_retry_returns_on_eventual_success():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("not ready")
        return "ok"

    assert smoke.retry(flaky, attempts=5, delay=0) == "ok"
    assert calls["n"] == 3


def test_retry_reraises_after_exhausting_attempts():
    def always_fail():
        raise RuntimeError("never ready")

    try:
        smoke.retry(always_fail, attempts=3, delay=0)
    except RuntimeError as exc:
        assert "never ready" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("retry should have re-raised")
