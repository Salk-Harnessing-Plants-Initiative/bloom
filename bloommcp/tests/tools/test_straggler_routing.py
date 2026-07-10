"""The former raw-CSV stragglers now read through the injected ExperimentReader.

Covers correlation_tools (cross-experiment reads via the port, honouring the
active adapter) and start_run source-CSV provenance (non-empty input_sha256
sourced from the reader, not a hard-coded TRAITS_DIR).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

import bloom_mcp.experiment_utils as eu
import bloom_mcp.storage_backend as sb
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.tools import _ports, correlation_tools


def _frame(geno_col, rep_col):
    return pd.DataFrame(
        {
            geno_col: ["a", "a", "b", "b", "c", "c", "d", "d"],
            rep_col: [1, 2, 1, 2, 1, 2, 1, 2],
            "t1": [1.0, 1.1, 2.0, 2.1, 3.0, 3.1, 4.0, 4.1],
            "t2": [5.0, 5.2, 6.0, 6.1, 7.0, 7.3, 8.0, 8.1],
        }
    )


def test_correlation_reads_flow_through_the_port(monkeypatch):
    """A seeded FakeReader (no filesystem) drives the correlation tool — proving
    the reads go through the injected adapter, not pd.read_csv(TRAITS_DIR/…)."""
    reader = FakeReader()
    reader.add_experiment("cylinder_traits.csv", _frame("Geno", "Rep"))
    reader.add_experiment("turface_traits.csv", _frame("geno", "rep"))
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        out = correlation_tools.run_cross_experiment_correlations()
        assert "Cross-Experiment Correlation" in out
        assert "Common genotypes: 4" in out

        listing = correlation_tools.list_experiments()
        assert "cylinder: 4 genotypes" in listing
        assert "FILE MISSING" not in listing
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_correlation_list_reports_missing_via_port():
    _ports.configure(reader=FakeReader(), store=FakeResultStore())
    try:
        out = correlation_tools.list_experiments()
        assert out.count("FILE MISSING") == 2  # neither experiment seeded
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_correlation_tools_no_longer_imports_pandas():
    """Structural guard: the tool no longer reads CSVs directly — it has no
    pandas import at all (the raw reads moved to align_experiments via the port)."""
    src = Path(correlation_tools.__file__).read_text()
    assert not re.search(r"^\s*import pandas", src, re.M)
    # and no direct TRAITS_DIR-joined read remains in code (docstring mention ok)
    assert "pd.read_csv(TRAITS_DIR" not in src.replace("``pd.read_csv(TRAITS_DIR / …)``", "")


def test_start_run_records_input_hash_via_reader(monkeypatch, tmp_path):
    """start_run sources source_csv from the active reader (LocalReader), so the
    committed manifest content-addresses the real input — no empty input_sha256."""
    from bloom_mcp.data_access import LocalReader
    from bloom_mcp.tools.workflows.qc import run_qc_workflow

    inp = tmp_path / "input"
    inp.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    rows = "".join(f"g{i},{float(i)},{float(i + 1)}\n" for i in range(12))
    (inp / "exp.csv").write_text("Genotype,trait_a,trait_b\n" + rows)

    monkeypatch.setattr(eu, "TRAITS_DIR", inp)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(store))
    monkeypatch.delenv("BLOOM_EXPERIMENT_LOCAL_ROOT", raising=False)
    sb.reset_backend_for_tests()
    _ports.configure(reader=LocalReader(), store=SupabaseResultStore())
    try:
        resp = run_qc_workflow("exp.csv")
        assert "error" not in resp, resp

        manifest = json.loads(
            (store / "bloommcp_output" / "qc_exp" / "manifest.json").read_bytes()
        )
        expected = hashlib.sha256((inp / "exp.csv").read_bytes()).hexdigest()
        assert manifest["experiment"]["input_sha256"] == expected
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
        sb.reset_backend_for_tests()


def test_qc_clean_sources_provenance_from_experiment_local_root(monkeypatch, tmp_path):
    """qc_clean self-stamps provenance and now sources source_csv through the active
    reader (``_ports.raw_source_for``), so a custom ``BLOOM_EXPERIMENT_LOCAL_ROOT``
    distinct from ``TRAITS_DIR`` is honoured — the committed manifest content-addresses
    the real input instead of silently recording an empty ``input_sha256``.

    Regression for the pre-fix hard-code (``local_src = TRAITS_DIR / experiment``):
    with the two roots divergent, that path resolved under the *empty* TRAITS_DIR,
    found nothing, and dropped the hash. TRAITS_DIR is monkeypatched to an empty dir
    so a reintroduced hard-code fails here.
    """
    from bloom_mcp.data_access import LocalReader
    from bloom_mcp.tools.qc_clean_tool import QCCleanParams, qc_clean

    exp_root = tmp_path / "experiments"  # BLOOM_EXPERIMENT_LOCAL_ROOT
    exp_root.mkdir()
    traits_dir = tmp_path / "traits"  # TRAITS_DIR — deliberately empty + divergent
    traits_dir.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    rows = "".join(f"g{i},{float(i)},{float(i + 1)}\n" for i in range(12))
    (exp_root / "exp.csv").write_text("Genotype,trait_a,trait_b\n" + rows)

    monkeypatch.setattr(eu, "TRAITS_DIR", traits_dir)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(store))
    monkeypatch.setenv("BLOOM_EXPERIMENT_LOCAL_ROOT", str(exp_root))
    sb.reset_backend_for_tests()
    _ports.configure(reader=LocalReader(), store=SupabaseResultStore())
    try:
        qc_clean(QCCleanParams(experiment="exp.csv"))

        manifest = json.loads(
            (store / "bloommcp_output" / "qc_exp" / "manifest.json").read_bytes()
        )
        expected = hashlib.sha256((exp_root / "exp.csv").read_bytes()).hexdigest()
        assert manifest["experiment"]["input_sha256"] == expected
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
        sb.reset_backend_for_tests()
