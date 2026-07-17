"""Fully-local mode: backend-aware boot gate, reader selection, and a real
offline ``qc_clean → pca_analysis`` run with a hard network guard.

The default (Supabase) path stays byte-for-byte unchanged; these tests assert the
opt-in ``BLOOM_STORAGE_BACKEND=local`` switch selects ``LocalReader``, drops the
Supabase boot gate, and still fails fast on the data dirs / an invalid backend.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import bloom_mcp.experiment_utils as eu
import bloom_mcp.storage_backend as sb


@pytest.fixture
def spy_run(monkeypatch):
    """Stub uvicorn.run so main() never binds a port; return the boot server module.

    main() calls ``uvicorn.run(build_app(), ...)`` directly (not ``mcp.run()``,
    which FastMCP no longer drives now that section apps are mounted onto a
    Starlette app in ``build_app()``) — patch the real entry point or main()
    binds a live port and blocks forever.
    """
    import uvicorn

    import bloom_mcp.server as server

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    return server


def _local_dirs(monkeypatch, tmp_path):
    """Point every required dir at existing temp dirs; select the local backend."""
    for var in ("BLOOM_TRAITS_DIR", "BLOOM_OUTPUT_DIR", "BLOOM_PLOTS_DIR"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://localhost/plots")
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr(eu, "TRAITS_DIR", tmp_path)
    monkeypatch.setattr(eu, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(eu, "PLOTS_DIR", tmp_path)
    sb.reset_backend_for_tests()


# ── reader selection ────────────────────────────────────────────────────────


def test_local_backend_wires_local_reader(spy_run, monkeypatch, tmp_path):
    from bloom_mcp.data_access import LocalReader
    from bloom_mcp.tools import _ports

    _local_dirs(monkeypatch, tmp_path)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("BLOOM_AGENT_KEY", raising=False)
    captured = {}
    monkeypatch.setattr(_ports, "configure", lambda **k: captured.update(k))

    spy_run.main()
    assert isinstance(captured["reader"], LocalReader)


def test_default_backend_wires_supabase_reader(spy_run, monkeypatch, tmp_path):
    from bloom_mcp.data_access import SupabaseReader
    from bloom_mcp.tools import _ports

    for var in ("BLOOM_TRAITS_DIR", "BLOOM_OUTPUT_DIR", "BLOOM_PLOTS_DIR"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://localhost/plots")
    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000")
    monkeypatch.setenv("BLOOM_AGENT_KEY", "fake-jwt")
    sb.reset_backend_for_tests()
    captured = {}
    monkeypatch.setattr(_ports, "configure", lambda **k: captured.update(k))

    spy_run.main()
    assert isinstance(captured["reader"], SupabaseReader)


# ── backend-aware boot gate ─────────────────────────────────────────────────


def test_fully_local_boot_needs_no_supabase(spy_run, monkeypatch, tmp_path):
    _local_dirs(monkeypatch, tmp_path)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("BLOOM_AGENT_KEY", raising=False)
    called = {"supa": False}
    monkeypatch.setattr(
        spy_run, "validate_supabase_env", lambda: called.__setitem__("supa", True)
    )

    spy_run.main()  # must not raise despite missing Supabase creds
    assert called["supa"] is False


def test_fully_local_boot_validates_local_input_root(spy_run, monkeypatch, tmp_path):
    _local_dirs(monkeypatch, tmp_path)
    missing = tmp_path / "nope"
    monkeypatch.setenv("BLOOM_EXPERIMENT_LOCAL_ROOT", str(missing))
    with pytest.raises(RuntimeError, match="(?i)local input root"):
        spy_run.main()


def test_fully_local_still_fails_fast_on_missing_data_dir(spy_run, monkeypatch, tmp_path):
    _local_dirs(monkeypatch, tmp_path)
    monkeypatch.delenv("BLOOM_PLOTS_URL", raising=False)
    with pytest.raises(RuntimeError, match="BLOOM_PLOTS_URL"):
        spy_run.main()


def test_invalid_backend_value_fails_fast(spy_run, monkeypatch, tmp_path):
    for var in ("BLOOM_TRAITS_DIR", "BLOOM_OUTPUT_DIR", "BLOOM_PLOTS_DIR"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://localhost/plots")
    monkeypatch.setattr(eu, "TRAITS_DIR", tmp_path)
    monkeypatch.setattr(eu, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(eu, "PLOTS_DIR", tmp_path)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "locel")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("BLOOM_AGENT_KEY", raising=False)
    sb.reset_backend_for_tests()
    with pytest.raises(RuntimeError, match="locel"):
        spy_run.main()


def test_default_backend_still_requires_supabase(spy_run, monkeypatch, tmp_path):
    for var in ("BLOOM_TRAITS_DIR", "BLOOM_OUTPUT_DIR", "BLOOM_PLOTS_DIR"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://localhost/plots")
    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("BLOOM_AGENT_KEY", raising=False)
    sb.reset_backend_for_tests()
    with pytest.raises(RuntimeError):
        spy_run.main()


# ── import purity (a fresh interpreter, no bloom env) ────────────────────────


def test_ports_import_is_pure_without_supabase_env():
    """_ports.py constructs SupabaseReader()/SupabaseResultStore() at module level;
    assert that import still succeeds with no Supabase env — no credential access
    at construction enforces the 'no Supabase at import' contract for the tools layer."""
    strip = (
        "SUPABASE_URL",
        "BLOOM_AGENT_KEY",
        "BLOOM_TRAITS_DIR",
        "BLOOM_OUTPUT_DIR",
        "BLOOM_PLOTS_DIR",
        "BLOOM_PLOTS_URL",
        "BLOOM_STORAGE_BACKEND",
        "BLOOM_STORAGE_LOCAL_ROOT",
        "BLOOM_EXPERIMENT_LOCAL_ROOT",
    )
    env = {k: v for k, v in os.environ.items() if k not in strip}
    result = subprocess.run(
        [sys.executable, "-c", "import bloom_mcp.tools._ports"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_server_import_is_pure_including_experiment_local_root():
    strip = (
        "SUPABASE_URL",
        "BLOOM_AGENT_KEY",
        "BLOOM_TRAITS_DIR",
        "BLOOM_OUTPUT_DIR",
        "BLOOM_PLOTS_DIR",
        "BLOOM_PLOTS_URL",
        "BLOOM_STORAGE_BACKEND",
        "BLOOM_STORAGE_LOCAL_ROOT",
        "BLOOM_EXPERIMENT_LOCAL_ROOT",
    )
    env = {k: v for k, v in os.environ.items() if k not in strip}
    result = subprocess.run(
        [sys.executable, "-c", "import bloom_mcp.server"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


# ── fully-local end-to-end: qc_clean → pca_analysis, no live Supabase ───────


@pytest.fixture
def reset_ports():
    """Restore the injected _ports reader/store after a test that swaps them.

    Also resets the memoized storage backend so the next test starts clean —
    avoids constructing a new SupabaseReader in teardown (which could raise if
    validation tightens) by saving/restoring the previous reader/store objects.
    """
    from bloom_mcp.tools import _ports

    prev_reader = _ports.reader()
    prev_store = _ports.store()
    yield
    _ports.configure(reader=prev_reader, store=prev_store)
    sb.reset_backend_for_tests()


def test_fully_local_qc_clean_to_pca_no_supabase(monkeypatch, tmp_path, reset_ports):
    """Offline I/O plumbing: LocalReader + SupabaseResultStore(local backend).

    Uses a small synthetic fixture (15 rows, 2 traits) so the test runs in
    seconds — scientific correctness of qc_clean / pca_analysis is covered by
    the oracle and tool-unit tests; here we only prove the wiring.
    """
    import bloom_mcp.supabase_client as sc
    from bloom_mcp.data_access import LocalReader
    from bloom_mcp.result_store import SupabaseResultStore
    from bloom_mcp.tools import _ports
    from bloom_mcp.sections.sleap_roots.analysis.pca_analysis import (
        PCAAnalysisParams,
        pca_analysis,
    )
    from bloom_mcp.sections.sleap_roots.analysis.qc_clean import (
        QCCleanParams,
        qc_clean,
    )

    inp = tmp_path / "input"
    inp.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    # 15 genotypes × 1 rep = 15 samples, 2 traits — above the min-samples
    # threshold (10) with no NaN/zero, so qc_clean keeps every trait and sample.
    # plant_id is a recognized SAMPLE_ID_PATTERNS name (#403) so qc_clean's
    # traceability requirement auto-detects it without a role override.
    rows = "".join(
        f"g{i},p{i},{float(i + 1)},{float(i * 2 + 1)}\n" for i in range(15)
    )
    (inp / "offline_e2e.csv").write_text("Genotype,plant_id,trait_a,trait_b\n" + rows)
    # Local input root == TRAITS_DIR so qc_clean's source_csv resolves too.
    monkeypatch.setattr(eu, "TRAITS_DIR", inp)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(store))
    monkeypatch.delenv("BLOOM_EXPERIMENT_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("BLOOM_AGENT_KEY", raising=False)
    sb.reset_backend_for_tests()

    # Hard network guard: any Supabase client construction fails the test.
    def _no_net(*a, **k):
        raise AssertionError("supabase.create_client called — the run hit the network")

    monkeypatch.setattr(sc.supabase, "create_client", _no_net)

    _ports.configure(reader=LocalReader(), store=SupabaseResultStore())

    qc_res = qc_clean(QCCleanParams(experiment="offline_e2e.csv"))
    assert qc_res.run_ref  # a persisted cleaned run

    cleaned = _ports.reader().load_experiment("offline_e2e.csv", require_clean=True)
    traits = list(cleaned.trait_cols)[:2]
    assert len(traits) >= 2

    pca_res = pca_analysis(
        PCAAnalysisParams(experiment="offline_e2e.csv", trait_columns=traits)
    )
    assert pca_res.n_components >= 1

    # Real files on disk under the local store root; nothing needed Supabase.
    manifests = list(store.rglob("manifest.json"))
    assert len(manifests) >= 2  # qc + pca
    assert list(store.rglob("_cleaned.csv"))
