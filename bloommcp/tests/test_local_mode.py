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


def test_fully_local_still_fails_fast_on_missing_data_dir(
    spy_run, monkeypatch, tmp_path
):
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
    at construction enforces the 'no Supabase at import' contract for the tools layer.
    """
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
    rows = "".join(f"g{i},p{i},{float(i + 1)},{float(i * 2 + 1)}\n" for i in range(15))
    (inp / "offline_e2e.csv").write_text("Genotype,plant_id,trait_a,trait_b\n" + rows)
    # Local input root == TRAITS_DIR so qc_clean's source_csv resolves too.
    monkeypatch.setattr(eu, "TRAITS_DIR", inp)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(store))
    monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost/output")
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


# ── BLOOM_LOCAL_ROOT (#479) ──────────────────────────────────────────────────


def _local_root_env(monkeypatch, tmp_path, **overrides):
    """Point BLOOM_STORAGE_BACKEND=local at a fresh BLOOM_LOCAL_ROOT, with none
    of the granular override vars set unless passed via ``overrides`` (e.g.
    ``BLOOM_EXPERIMENT_LOCAL_ROOT=some_path``).
    """
    for var in (
        "BLOOM_TRAITS_DIR",
        "BLOOM_OUTPUT_DIR",
        "BLOOM_PLOTS_DIR",
        "BLOOM_EXPERIMENT_LOCAL_ROOT",
        "BLOOM_STORAGE_LOCAL_ROOT",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://localhost/plots")
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    root = tmp_path / "local_root"
    root.mkdir()
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    for k, v in overrides.items():
        monkeypatch.setenv(k, str(v))
    sb.reset_backend_for_tests()
    # PLOTS_DIR is a frozen module constant (resolved once at import); simulate
    # what it resolves to given this env, matching this file's existing
    # convention of monkeypatching the constant directly (see _local_dirs above).
    monkeypatch.setattr(eu, "PLOTS_DIR", Path(eu._resolve_plots_dir()))
    return root


# ── precedence: input / plots resolvers ─────────────────────────────────────


def test_resolve_experiment_local_root_prefers_explicit_override(monkeypatch, tmp_path):
    explicit = tmp_path / "explicit_input"
    explicit.mkdir()
    _local_root_env(monkeypatch, tmp_path, BLOOM_EXPERIMENT_LOCAL_ROOT=explicit)
    assert eu.resolve_experiment_local_root() == explicit


def test_resolve_experiment_local_root_uses_local_root_default(monkeypatch, tmp_path):
    root = _local_root_env(monkeypatch, tmp_path)
    assert eu.resolve_experiment_local_root() == root / "input"


def test_resolve_experiment_local_root_falls_back_to_traits_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("BLOOM_EXPERIMENT_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("BLOOM_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setattr(eu, "TRAITS_DIR", tmp_path)
    sb.reset_backend_for_tests()
    assert eu.resolve_experiment_local_root() == tmp_path


def test_plots_dir_resolves_under_local_root(monkeypatch, tmp_path):
    root = tmp_path / "local_root"
    root.mkdir()
    monkeypatch.delenv("BLOOM_PLOTS_DIR", raising=False)
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    sb.reset_backend_for_tests()
    assert eu._resolve_plots_dir() == str(root / "plots")


def test_plots_dir_explicit_override_wins_over_local_root(monkeypatch, tmp_path):
    root = tmp_path / "local_root"
    root.mkdir()
    explicit = tmp_path / "explicit_plots"
    monkeypatch.setenv("BLOOM_PLOTS_DIR", str(explicit))
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    sb.reset_backend_for_tests()
    assert eu._resolve_plots_dir() == str(explicit)


def test_plots_dir_ignores_local_root_on_default_backend(monkeypatch, tmp_path):
    root = tmp_path / "local_root"
    root.mkdir()
    monkeypatch.delenv("BLOOM_PLOTS_DIR", raising=False)
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)  # default: supabase
    sb.reset_backend_for_tests()
    assert eu._resolve_plots_dir() == ""


# ── BLOOM_PLOTS_URL self-serve default under BLOOM_LOCAL_ROOT (#642) ────────


def test_plots_url_resolves_under_local_root(monkeypatch, tmp_path):
    root = tmp_path / "local_root"
    root.mkdir()
    monkeypatch.delenv("BLOOM_PLOTS_URL", raising=False)
    monkeypatch.delenv("BLOOMMCP_PUBLIC_URL", raising=False)
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    sb.reset_backend_for_tests()
    assert eu._resolve_plots_url() == "http://localhost:8811/plots"


def test_plots_url_explicit_override_wins_over_local_root(monkeypatch, tmp_path):
    root = tmp_path / "local_root"
    root.mkdir()
    monkeypatch.setenv("BLOOM_PLOTS_URL", "http://elsewhere:9000/plots")
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    sb.reset_backend_for_tests()
    assert eu._resolve_plots_url() == "http://elsewhere:9000/plots"


def test_plots_url_ignores_local_root_on_default_backend(monkeypatch, tmp_path):
    root = tmp_path / "local_root"
    root.mkdir()
    monkeypatch.delenv("BLOOM_PLOTS_URL", raising=False)
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)  # default: supabase
    sb.reset_backend_for_tests()
    assert eu._resolve_plots_url() == ""


def test_plots_url_ignores_explicit_plots_dir_without_local_root(monkeypatch, tmp_path):
    """Granular explicit-override tier (no BLOOM_LOCAL_ROOT): BLOOM_PLOTS_URL
    stays unconditionally required/unaffected, even with backend=local."""
    monkeypatch.delenv("BLOOM_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_PLOTS_DIR", str(tmp_path))
    monkeypatch.delenv("BLOOM_PLOTS_URL", raising=False)
    sb.reset_backend_for_tests()
    assert eu._resolve_plots_url() == ""


# ── BLOOM_LOCAL_ROOT top-level validation ───────────────────────────────────


def test_validate_env_fails_when_local_root_missing(monkeypatch, tmp_path):
    _local_root_env(monkeypatch, tmp_path)
    missing = tmp_path / "does_not_exist"
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(missing))
    with pytest.raises(RuntimeError, match="BLOOM_LOCAL_ROOT.*does not exist"):
        eu.validate_env()


def test_validate_env_fails_when_local_root_is_a_file(monkeypatch, tmp_path):
    _local_root_env(monkeypatch, tmp_path)
    a_file = tmp_path / "local_root_file"
    a_file.write_text("x")
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(a_file))
    with pytest.raises(RuntimeError, match="BLOOM_LOCAL_ROOT.*not a directory"):
        eu.validate_env()


def test_validate_env_fails_when_local_root_not_writable(monkeypatch, tmp_path):
    root = _local_root_env(monkeypatch, tmp_path)
    try:
        root.chmod(0o500)
        if os.access(root, os.W_OK):
            pytest.skip("cannot restrict write access on this platform/privilege")
        with pytest.raises(RuntimeError, match="BLOOM_LOCAL_ROOT.*not writable"):
            eu.validate_env()
    finally:
        root.chmod(0o700)


# ── conditionally-optional legacy vars + auto-create ────────────────────────


def test_validate_env_succeeds_with_only_local_root_set(monkeypatch, tmp_path):
    """The three legacy dir vars are unset entirely (not merely pointed at a bad
    path) — validate_env() must not raise "Missing required environment
    variables"."""
    _local_root_env(monkeypatch, tmp_path)
    eu.validate_env()  # must not raise


def test_validate_env_and_experiment_root_create_subfolders(monkeypatch, tmp_path):
    root = _local_root_env(monkeypatch, tmp_path)
    assert not (root / "input").exists()
    assert not (root / "output").exists()
    assert not (root / "plots").exists()
    eu.validate_env()
    eu.validate_experiment_local_root()
    assert (root / "input").is_dir()
    assert (root / "output").is_dir()
    assert (root / "plots").is_dir()


def test_mixed_precedence_across_subpaths(monkeypatch, tmp_path):
    """One subpath's explicit override doesn't disturb the other two subpaths'
    BLOOM_LOCAL_ROOT-derived resolution or auto-create in the same boot."""
    explicit_input = tmp_path / "explicit_input"
    explicit_input.mkdir()
    root = _local_root_env(
        monkeypatch, tmp_path, BLOOM_EXPERIMENT_LOCAL_ROOT=explicit_input
    )
    assert eu.resolve_experiment_local_root() == explicit_input
    eu.validate_env()
    eu.validate_experiment_local_root()
    assert (root / "output").is_dir()
    assert (root / "plots").is_dir()
    assert not (root / "input").exists()  # never touched — explicit wins


# ── explicit overrides keep the strict must-exist contract ─────────────────


def test_explicit_experiment_root_still_requires_pre_existence(monkeypatch, tmp_path):
    missing = tmp_path / "nope"
    _local_root_env(monkeypatch, tmp_path, BLOOM_EXPERIMENT_LOCAL_ROOT=missing)
    with pytest.raises(RuntimeError, match="does not exist|not a directory"):
        eu.validate_experiment_local_root()


def test_explicit_storage_root_still_requires_pre_existence(monkeypatch, tmp_path):
    missing = tmp_path / "nope"
    _local_root_env(monkeypatch, tmp_path, BLOOM_STORAGE_LOCAL_ROOT=missing)
    with pytest.raises(RuntimeError, match="does not exist"):
        eu.validate_env()


def test_explicit_plots_dir_still_requires_pre_existence(monkeypatch, tmp_path):
    missing = tmp_path / "nope"
    _local_root_env(monkeypatch, tmp_path, BLOOM_PLOTS_DIR=missing)
    with pytest.raises(RuntimeError, match="does not exist"):
        eu.validate_env()


# ── a derived subfolder blocked by a non-directory file ─────────────────────


def test_input_subfolder_blocked_by_file_raises_clearly(monkeypatch, tmp_path):
    root = _local_root_env(monkeypatch, tmp_path)
    (root / "input").write_text("blocking file")
    with pytest.raises(RuntimeError, match="input root.*not a directory"):
        eu.validate_experiment_local_root()


def test_output_subfolder_blocked_by_file_raises_clearly(monkeypatch, tmp_path):
    root = _local_root_env(monkeypatch, tmp_path)
    (root / "output").write_text("blocking file")
    with pytest.raises(RuntimeError, match="output root.*not a directory"):
        eu.validate_env()


def test_plots_subfolder_blocked_by_file_raises_clearly(monkeypatch, tmp_path):
    root = _local_root_env(monkeypatch, tmp_path)
    (root / "plots").write_text("blocking file")
    with pytest.raises(RuntimeError, match="plots root.*not a directory"):
        eu.validate_env()


def test_plots_subfolder_not_writable_raises_at_boot(monkeypatch, tmp_path):
    """A pre-existing, non-writable plots/ dir must fail at boot (like input and
    output already do), not surface a raw PermissionError later in save_plot()."""
    root = _local_root_env(monkeypatch, tmp_path)
    plots = root / "plots"
    plots.mkdir()
    try:
        plots.chmod(0o500)
        if os.access(plots, os.W_OK):
            pytest.skip("cannot restrict write access on this platform/privilege")
        with pytest.raises(RuntimeError, match="plots root.*not writable"):
            eu.validate_env()
    finally:
        plots.chmod(0o700)


# ── default-path regression: BLOOM_LOCAL_ROOT is inert without local backend ──


def test_default_backend_ignores_local_root_entirely(monkeypatch, tmp_path):
    """BLOOM_STORAGE_BACKEND unset/supabase with BLOOM_LOCAL_ROOT set anyway: all
    three resolvers ignore BLOOM_LOCAL_ROOT, and the three legacy vars remain
    required — byte-for-byte unchanged default-path behavior."""
    root = tmp_path / "local_root"
    root.mkdir()
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("BLOOM_EXPERIMENT_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("BLOOM_STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.delenv("BLOOM_PLOTS_DIR", raising=False)
    monkeypatch.delenv("BLOOM_OUTPUT_DIR", raising=False)
    sb.reset_backend_for_tests()

    assert eu._resolve_plots_dir() == ""
    monkeypatch.setattr(eu, "TRAITS_DIR", Path("/nonexistent-traits"))
    assert eu.resolve_experiment_local_root() == Path("/nonexistent-traits")
    assert sb._resolve_local_root() == Path("")  # BLOOM_OUTPUT_DIR unset too

    for var in (
        "BLOOM_TRAITS_DIR",
        "BLOOM_OUTPUT_DIR",
        "BLOOM_PLOTS_DIR",
        "BLOOM_PLOTS_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="BLOOM_TRAITS_DIR"):
        eu.validate_env()


# ── import purity extension + precise gating regression guard ──────────────


def test_import_succeeds_with_local_root_set_and_invalid_backend():
    """Mirrors test_server_import_is_pure_with_invalid_backend, extended: the
    opt-in BLOOM_LOCAL_ROOT read (is_local_backend() never raises) cannot itself
    turn an invalid BLOOM_STORAGE_BACKEND value into an import-time crash."""
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
        "BLOOM_LOCAL_ROOT",
    )
    env = {k: v for k, v in os.environ.items() if k not in strip}
    env["BLOOM_LOCAL_ROOT"] = "/tmp/wherever-479"
    env["BLOOM_STORAGE_BACKEND"] = "locel"
    result = subprocess.run(
        [sys.executable, "-c", "import bloom_mcp.server"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_is_local_backend_not_consulted_when_local_root_unset(monkeypatch):
    """Pins the gating precisely: PLOTS_DIR's default resolution must not reach
    is_local_backend() at all when BLOOM_LOCAL_ROOT is unset — a future
    regression that reordered the checks would defeat the "no observable change
    without opt-in" guarantee even though is_local_backend() itself never raises
    (so an import-succeeds/return-code check alone wouldn't catch it)."""
    monkeypatch.delenv("BLOOM_LOCAL_ROOT", raising=False)

    def _boom():
        raise AssertionError(
            "is_local_backend() was called despite BLOOM_LOCAL_ROOT being unset"
        )

    monkeypatch.setattr(sb, "is_local_backend", _boom)
    assert eu._resolve_plots_dir() == os.getenv("BLOOM_PLOTS_DIR", "")
    assert eu._fully_local_root() is None


def test_is_local_backend_not_consulted_when_local_root_unset_for_plots_url(monkeypatch):
    """Same pin as test_is_local_backend_not_consulted_when_local_root_unset,
    for _resolve_plots_url()'s reuse of the same _fully_local_root() gate."""
    monkeypatch.delenv("BLOOM_LOCAL_ROOT", raising=False)

    def _boom():
        raise AssertionError(
            "is_local_backend() was called despite BLOOM_LOCAL_ROOT being unset"
        )

    monkeypatch.setattr(sb, "is_local_backend", _boom)
    assert eu._resolve_plots_url() == os.getenv("BLOOM_PLOTS_URL", "")


def test_plots_dir_module_constant_reflects_local_root_at_real_import(tmp_path):
    """Exercises the REAL import-time wiring of PLOTS_DIR in a fresh interpreter
    — every other test either calls _resolve_plots_dir() directly or manually
    monkeypatches PLOTS_DIR to the value it should resolve to, neither of which
    would catch a regression in the actual `PLOTS_DIR = Path(_resolve_plots_dir())`
    module-level binding itself (verified by fault injection: reverting that line
    to the pre-#479 `Path(os.getenv("BLOOM_PLOTS_DIR", ""))` form leaves every
    other test in this file green)."""
    root = tmp_path / "local_root"
    root.mkdir()
    strip = (
        "BLOOM_TRAITS_DIR",
        "BLOOM_OUTPUT_DIR",
        "BLOOM_PLOTS_DIR",
        "BLOOM_PLOTS_URL",
        "BLOOM_STORAGE_LOCAL_ROOT",
        "BLOOM_EXPERIMENT_LOCAL_ROOT",
        "SUPABASE_URL",
        "BLOOM_AGENT_KEY",
    )
    env = {k: v for k, v in os.environ.items() if k not in strip}
    env["BLOOM_STORAGE_BACKEND"] = "local"
    env["BLOOM_LOCAL_ROOT"] = str(root)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import bloom_mcp.experiment_utils as eu; "
            "import pathlib, os; "
            "expected = pathlib.Path(os.environ['BLOOM_LOCAL_ROOT']) / 'plots'; "
            "assert eu.PLOTS_DIR == expected, (eu.PLOTS_DIR, expected)",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


# ── fully-local end-to-end via BLOOM_LOCAL_ROOT alone ───────────────────────


def test_fully_local_qc_clean_to_pca_via_local_root_only(
    monkeypatch, tmp_path, reset_ports
):
    """Same offline round-trip as test_fully_local_qc_clean_to_pca_no_supabase,
    but driven by ONLY BLOOM_STORAGE_BACKEND=local + BLOOM_LOCAL_ROOT — no
    BLOOM_TRAITS_DIR / BLOOM_OUTPUT_DIR / BLOOM_PLOTS_DIR /
    BLOOM_EXPERIMENT_LOCAL_ROOT / BLOOM_STORAGE_LOCAL_ROOT set at all (#479)."""
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

    root = _local_root_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost/output")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("BLOOM_AGENT_KEY", raising=False)

    # Boot-time validation creates input/output/plots under BLOOM_LOCAL_ROOT,
    # matching server.main()'s real ordering (validate_data_env() then, in the
    # fully-local branch, validate_experiment_local_root()).
    eu.validate_env()
    eu.validate_experiment_local_root()

    rows = "".join(f"g{i},p{i},{float(i + 1)},{float(i * 2 + 1)}\n" for i in range(15))
    (root / "input" / "offline_e2e.csv").write_text(
        "Genotype,plant_id,trait_a,trait_b\n" + rows
    )

    def _no_net(*a, **k):
        raise AssertionError("supabase.create_client called — the run hit the network")

    monkeypatch.setattr(sc.supabase, "create_client", _no_net)

    _ports.configure(reader=LocalReader(), store=SupabaseResultStore())

    qc_res = qc_clean(QCCleanParams(experiment="offline_e2e.csv"))
    assert qc_res.run_ref

    cleaned = _ports.reader().load_experiment("offline_e2e.csv", require_clean=True)
    traits = list(cleaned.trait_cols)[:2]
    assert len(traits) >= 2

    pca_res = pca_analysis(
        PCAAnalysisParams(experiment="offline_e2e.csv", trait_columns=traits)
    )
    assert pca_res.n_components >= 1

    manifests = list((root / "output").rglob("manifest.json"))
    assert len(manifests) >= 2  # qc + pca
    assert list((root / "output").rglob("_cleaned.csv"))
    assert (root / "plots").is_dir()
