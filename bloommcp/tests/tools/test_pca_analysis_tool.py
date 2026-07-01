"""Contract + oracle tests for the granular ``pca_analysis`` tool (Tier 4 / #308).

Five contract patterns + the #120 turface_19 golden PCA reproduced **through the MCP
tool**, plus the consumer guarantees the tool exists to provide: it reads a *cleaned*
experiment (``require_clean=True``), selects only certified-clean traits (so the delegate
never silently ``dropna()``s), delegates ALL PCA to
``sleap_roots_analyze.perform_pca_analysis`` (typed via ``PCAResult.from_pca_dict``), is
deterministic (``seed = None``), and persists a versioned ``pca`` run — returning a variance
summary + links, never the matrices inline.

The unit oracle seeds a cleaned version directly via ``FakeReader.add_cleaned_version`` (the
reader/store fakes are disjoint), so these tests do not depend on a live ``qc_clean`` run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.tools import _ports
from bloom_mcp.tools import pca_analysis_tool
from bloom_mcp.tools.pca_analysis_tool import (
    PCAAnalysisParams,
    PCAAnalysisResult,
    pca_analysis,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_FINAL = _FIXTURES / "turface_19_final_data.csv"
_GOLDEN = json.loads((_FIXTURES / "turface_19_pca_golden.json").read_text())

_EXPERIMENT = "turface_19.csv"
_TRAITS = _GOLDEN["trait_cols"]  # the recorded 8-trait PCA selection
_VAR_TOL = (
    1e-6  # matches tests/test_oracle.py — safe: deterministic solver, no randomness
)


def _final_df() -> pd.DataFrame:
    return pd.read_csv(_FINAL)


@pytest.fixture
def injected_ports():
    """FakeReader serving the cleaned fixture as a cleaned version + FakeResultStore."""
    reader = FakeReader()
    store = FakeResultStore()
    # Seed the post-QC fixture as a *cleaned* version so require_clean=True resolves it.
    reader.add_cleaned_version(_EXPERIMENT, "v1", _final_df(), make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _run(**overrides) -> PCAAnalysisResult:
    params = {"experiment": _EXPERIMENT, "trait_columns": _TRAITS, **overrides}
    return pca_analysis(PCAAnalysisParams(**params))


# ── 2. Golden PCA through the tool (north star) ─────────────────────────────


def test_golden_pca_through_the_tool(injected_ports):
    """2.2 — reproduce the #120 turface_19 golden through the MCP boundary."""
    result = _run()

    # Independent oracle: n=3 + cumulative variance (from #120 viz_pca_metadata.json).
    assert result.n_components == 3 == _GOLDEN["n_pca_components"]
    assert result.cumulative_variance_ratio[2] == pytest.approx(
        _GOLDEN["pca_explained_variance"], abs=_VAR_TOL
    )
    # Per-PC drift snapshot (characterization; read the key, don't hard-code literals).
    assert result.explained_variance_ratio == pytest.approx(
        _GOLDEN["pca_explained_variance_ratio"], abs=_VAR_TOL
    )
    assert result.feature_names == _TRAITS


def test_no_silent_sample_loss(injected_ports):
    """2.3 — PCA runs on the full certified sample set; no silent dropna()."""
    result = _run()
    assert result.n_samples == len(_final_df()) == 153
    assert result.n_features == len(_TRAITS) == 8


# ── 3.1 tools/list presence ─────────────────────────────────────────────────


def test_pca_analysis_in_tools_list_and_workflow_preserved():
    """3.1 — pca_analysis is discoverable; the dimred workflow is still registered."""
    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "pca_analysis" in tools
    assert tools["pca_analysis"].inputSchema is not None
    assert "run_dimensionality_reduction_workflow" in tools  # additive — not removed


# ── 3.2 delegation pinning (spy) ────────────────────────────────────────────


def test_delegates_once_and_never_calls_vendored_pca(injected_ports, monkeypatch):
    captured = {}
    real = pca_analysis_tool.perform_pca_analysis

    def _spy(data, **kwargs):
        captured["n_calls"] = captured.get("n_calls", 0) + 1
        captured["columns"] = list(data.columns)
        captured["kwargs"] = kwargs
        return real(data, **kwargs)

    monkeypatch.setattr(pca_analysis_tool, "perform_pca_analysis", _spy)

    import bloom_mcp.pca as vendored

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("pca_analysis called the vendored bloom_mcp.pca")

    monkeypatch.setattr(vendored, "perform_pca_analysis", _boom)

    _run()

    assert captured["n_calls"] == 1
    assert captured["columns"] == _TRAITS  # the validated certified subset, in order


# ── 3.3 n_components override vs threshold + clamp ──────────────────────────


def test_n_components_override_and_clamp(injected_ports):
    # Explicit n_components overrides the variance threshold.
    assert _run(n_components=2).n_components == 2
    # Omitted → threshold-based selection (the golden's 3).
    assert _run(n_components=None).n_components == 3
    # Larger than the feature count → the delegate clamps, never raises.
    assert _run(n_components=99).n_components == len(_TRAITS) == 8


# ── 3.4 schema round-trip ───────────────────────────────────────────────────


def test_valid_input_output_round_trip(injected_ports):
    result = _run()
    again = PCAAnalysisResult.model_validate(json.loads(result.model_dump_json()))
    assert again.explained_variance_ratio == result.explained_variance_ratio


# ── 3.5 invalid input — out of range ────────────────────────────────────────


def test_threshold_out_of_range_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        pca_analysis({"experiment": _EXPERIMENT, "explained_variance_threshold": 1.5})
    assert exc.value.code == "invalid_input"


def test_n_components_below_one_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        pca_analysis({"experiment": _EXPERIMENT, "n_components": 0})
    assert exc.value.code == "invalid_input"


# ── 3.6 trait-column validation (closes the require_clean NaN hole) ─────────


def test_unknown_trait_column_is_invalid_input_naming_it(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["NoSuchTrait"])
    assert exc.value.code == "invalid_input"
    assert "NoSuchTrait" in exc.value.message


def test_non_certified_numeric_column_is_rejected_not_dropped(
    injected_ports, monkeypatch
):
    """A numeric column present in the frame but OUTSIDE the certified-clean trait set
    (here ``Replicate``, a metadata column) must be rejected up front — never passed to
    the delegate where it could silently drop rows."""
    called = {"n": 0}

    def _spy(*a, **k):  # pragma: no cover - must not run
        called["n"] += 1
        raise AssertionError("delegate called with a non-certified column")

    monkeypatch.setattr(pca_analysis_tool, "perform_pca_analysis", _spy)

    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["Replicate"])
    assert exc.value.code == "invalid_input"
    assert "Replicate" in exc.value.message
    assert called["n"] == 0


# ── 3.7 degenerate fit → structured, not internal; no leak; no run ──────────


def test_degenerate_fit_is_structured_without_leaking(injected_ports, monkeypatch):
    _reader, store = injected_ports

    def _boom(*a, **k):
        raise ValueError("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(pca_analysis_tool, "perform_pca_analysis", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "assumption_violated"
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "/var" not in msg and "db.internal" not in msg
    assert store.list_runs(_EXPERIMENT, "pca") == []  # nothing persisted


def test_real_delegate_degenerate_selection_is_assumption_violated(injected_ports):
    """The REAL delegate raises ValueError on a constant/degenerate selection; it must
    surface as a self-correctable assumption_violated, not the contract's internal_error.
    """
    reader, store = injected_ports
    # A cleaned frame whose only traits are constant → no non-zero-variance column.
    const = pd.DataFrame({"tA": [5.0] * 6, "tB": [5.0] * 6})
    reader.add_cleaned_version("const.csv", "v1", const, make_latest=True)
    with pytest.raises(BloomMCPError) as exc:
        pca_analysis(PCAAnalysisParams(experiment="const.csv"))
    assert exc.value.code == "assumption_violated"
    assert store.list_runs("const.csv", "pca") == []


# ── 3.8 require_clean consumption (property / invariant) ────────────────────


def test_raw_only_experiment_is_rejected_with_qc_clean_remedy():
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_experiment("rawonly.csv", _final_df())  # raw only, no cleaned version
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            pca_analysis(PCAAnalysisParams(experiment="rawonly.csv"))
        assert "qc_clean" in exc.value.remedy.lower()
        assert store.list_runs("rawonly.csv", "pca") == []
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_consumes_cleaned_version_source(injected_ports):
    result = _run()
    assert result.source == "v1_cleaned"
    assert result.source != "raw"


# ── 3.9 provenance: deterministic, seed None ────────────────────────────────


def test_provenance_seed_none(injected_ports):
    _reader, store = injected_ports
    _run()
    stored = store.get_run(_EXPERIMENT, "pca", "latest")
    assert stored.tool == "pca_analysis"
    assert stored.seed is None  # PCA here is deterministic — no random_state


# ── 3.10 determinism ────────────────────────────────────────────────────────


def test_repeated_runs_are_identical(injected_ports):
    a = _run()
    b = _run()
    assert a.explained_variance_ratio == pytest.approx(
        b.explained_variance_ratio, abs=_VAR_TOL
    )
    assert a.cumulative_variance_ratio == pytest.approx(
        b.cumulative_variance_ratio, abs=_VAR_TOL
    )


# ── 5.2 persist: links (not blobs) + outputs set + version increments ───────


def test_persists_artifacts_and_returns_links_not_matrices(injected_ports):
    _reader, store = injected_ports
    result = _run()

    stored = store.get_run(_EXPERIMENT, "pca", "latest")
    assert set(stored.output_keys) == {"loadings.csv", "scores.csv", "pca_result.json"}
    assert set(result.outputs) == {"loadings.csv", "scores.csv", "pca_result.json"}
    assert result.run_ref == stored.run_ref
    assert result.manifest_path == stored.manifest_path

    # Links, not blobs: no field carries the N×k score / loadings matrices inline.
    assert not hasattr(result, "scores") and not hasattr(result, "loadings")
    dumped = result.model_dump()
    assert not any(
        isinstance(v, (list, dict)) and len(str(v)) > 5000 for v in dumped.values()
    )


def test_second_run_increments_version(injected_ports):
    _reader, store = injected_ports
    _run()
    _run()
    assert [r.run_ref for r in store.list_runs(_EXPERIMENT, "pca")] == ["v1", "v2"]
    assert store.get_run(_EXPERIMENT, "pca", "latest").run_ref == "v2"
