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
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, RunStateError, SupabaseResultStore
from bloom_mcp.tools import _ports
from bloom_mcp.sections.sleap_roots.analysis import pca_analysis as pca_analysis_tool
from bloom_mcp.sections.sleap_roots.analysis.pca_analysis import (
    PCAAnalysisParams,
    PCAAnalysisResult,
    pca_analysis,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_FINAL = _FIXTURES / "turface_19_final_data.csv"
_GOLDEN = json.loads(
    (_FIXTURES / "turface_19_pca_golden.json").read_text(encoding="utf-8")
)

_EXPERIMENT = "turface_19.csv"
_TRAITS = _GOLDEN["trait_cols"]  # the recorded 8-trait PCA selection
_VAR_TOL = (
    1e-6  # matches tests/test_oracle.py — safe: deterministic solver, no randomness
)


def _final_df() -> pd.DataFrame:
    return pd.read_csv(_FINAL, encoding="utf-8")


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


def test_pca_analysis_in_tools_list():
    """3.1 — pca_analysis is discoverable."""
    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "sleap_roots_pca_analysis" in tools
    assert tools["sleap_roots_pca_analysis"].inputSchema is not None


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


def test_undeclared_delegate_raise_is_scrubbed(injected_ports, monkeypatch):
    """bloom#664 item 2: a delegate exception type outside the `ValueError`-only
    except clause above falls through undeclared to `internal_error` — pinned,
    not just "doesn't leak" (mirrors the #660 qc_inspect/qc_clean/remove_outliers
    pattern, closing the coverage gap for this tool)."""
    _reader, store = injected_ports

    def _boom(*a, **k):
        raise RuntimeError("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(pca_analysis_tool, "perform_pca_analysis", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "internal_error"
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "/var" not in msg and "db.internal" not in msg
    assert store.list_runs(_EXPERIMENT, "pca") == []


# ── ResultStore write-path failures surface as tool_error, not a bare internal_error ref
# (#640: pca_analysis's declared errors=(ExperimentReadError,) swallowed a CommitFailedError/
# ManifestReadError from store.create_run()/commit() into a generic internal_error ref) ──


def test_commit_failure_surfaces_as_tool_error(injected_ports):
    _reader, store = injected_ports
    store.fail_next_commit(_EXPERIMENT, "pca")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "commit failed for pca" in exc.value.message


def test_manifest_read_failure_surfaces_as_tool_error(injected_ports):
    _reader, store = injected_ports
    store.fail_next_read(_EXPERIMENT, "pca")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "manifest read failure" in exc.value.message


def test_run_state_error_from_commit_still_maps_to_internal_error(
    injected_ports, monkeypatch
):
    """RunStateError (a handle-misuse/wiring bug, never triggerable via tool input) must
    stay internal_error even after declaring CommitFailedError/ManifestReadError — proves
    the errors= tuple wasn't accidentally widened to the full ResultStoreError base
    (design.md Decision 1; #660 review: only qc_inspect had this test)."""
    _reader, store = injected_ports

    def _boom(run, outputs):
        raise RunStateError("commit() on an unknown or already-committed run")

    monkeypatch.setattr(store, "commit", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "internal_error"


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

    # bloom#581: a signed link + hash + size per output, on the tool's own result
    # (get_run's stored above never carries them — see Decision 1).
    assert set(result.output_links) == set(result.outputs)
    for name, key in result.outputs.items():
        link = result.output_links[name]
        assert link.key == key
        assert link.url
        assert link.sha256 == stored.output_sha256[name]
        assert link.size_bytes >= 0
    assert stored.output_links == {}

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


# ── 9. Review hardening — silent-inconsistency + provenance gaps (PR #377) ───


def _capture_staged_outputs(store, monkeypatch) -> dict[str, str]:
    """Read each staged artifact's text at commit time (before the fake store rmtree's it)."""
    captured: dict[str, str] = {}
    real_commit = store.commit

    def _commit(run, outputs):
        for name in outputs:
            captured[name] = (run.staging_dir / name).read_text(encoding="utf-8")
        return real_commit(run, outputs)

    monkeypatch.setattr(store, "commit", _commit)
    return captured


# 9.2 — an explicitly empty selection is rejected, not treated as "all traits"


def test_empty_trait_columns_is_invalid_input(injected_ports, monkeypatch):
    _reader, store = injected_ports

    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called on an empty selection")

    monkeypatch.setattr(pca_analysis_tool, "perform_pca_analysis", _spy)

    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=[])
    assert exc.value.code == "invalid_input"
    assert store.list_runs(_EXPERIMENT, "pca") == []


# 9.3 — duplicate names are rejected before they inflate the feature set


def test_duplicate_trait_columns_is_invalid_input_naming_them(
    injected_ports, monkeypatch
):
    _reader, store = injected_ports

    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called with duplicate columns")

    monkeypatch.setattr(pca_analysis_tool, "perform_pca_analysis", _spy)

    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=[_TRAITS[0], _TRAITS[0]])
    assert exc.value.code == "invalid_input"
    assert _TRAITS[0] in exc.value.message
    assert store.list_runs(_EXPERIMENT, "pca") == []


# 9.4 — a constant certified trait the delegate would silently drop is surfaced


def test_constant_certified_trait_is_reported_not_raised(injected_ports):
    """#412 — a constant trait the delegate silently drops is reported via
    ``dropped_constant_traits``, not raised as ``assumption_violated``, as long as
    enough non-constant traits remain to fit (see
    :func:`test_real_delegate_degenerate_selection_is_assumption_violated` above for
    the genuine no-non-constant-trait-survives case, which still raises)."""
    reader, store = injected_ports
    # Two varying traits (a real fit is reachable) + one constant trait the delegate drops.
    mixed = pd.DataFrame(
        {
            "tA": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "tB": [2.0, 1.0, 5.0, 3.0, 8.0, 4.0],
            "tConst": [7.0] * 6,
        }
    )
    reader.add_cleaned_version("mixed.csv", "v1", mixed, make_latest=True)

    result = pca_analysis(PCAAnalysisParams(experiment="mixed.csv"))

    assert result.dropped_constant_traits == ["tConst"]
    assert result.n_features == len(result.feature_names) == 2
    assert "tConst" not in result.feature_names
    # The run IS persisted — the artifact is internally consistent (n_features /
    # feature_names / the persisted loadings all reflect the same post-drop set).
    assert store.list_runs("mixed.csv", "pca") != []


def test_n_features_equals_fitted_feature_count(injected_ports):
    """The reported n_features is the count actually fit, never the requested count."""
    result = _run()
    assert result.n_features == len(result.feature_names) == 8


# 9.6 — a non-finite value that dropna() would keep is rejected, not fit


def test_non_finite_certified_trait_is_rejected(injected_ports, monkeypatch):
    reader, store = injected_ports
    inf_df = pd.DataFrame(
        {
            "tA": [1.0, 2.0, 3.0, 4.0],
            "tB": [4.0, float("inf"), 2.0, 1.0],
        }
    )
    reader.add_cleaned_version("inf.csv", "v1", inf_df, make_latest=True)

    def _spy(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("delegate called with a non-finite value")

    monkeypatch.setattr(pca_analysis_tool, "perform_pca_analysis", _spy)

    with pytest.raises(BloomMCPError) as exc:
        pca_analysis(PCAAnalysisParams(experiment="inf.csv"))
    assert exc.value.code == "assumption_violated"
    assert store.list_runs("inf.csv", "pca") == []


# 9.1 — persisted scores carry sample identity (traceability, not positional alignment)


def test_scores_csv_carries_sample_identity(injected_ports, monkeypatch):
    _reader, store = injected_ports
    captured = _capture_staged_outputs(store, monkeypatch)
    _run()

    scores = pd.read_csv(io.StringIO(captured["scores.csv"]))
    # Identity/metadata columns prefix a trailing block of PC columns. As of #403
    # trait detection delegates to get_trait_columns, so the numeric metadata column
    # Computation.Time.s is (correctly) classified as metadata and carried here too —
    # it is NOT a PC. The role columns still lead, and the PCs are the trailing block.
    assert list(scores.columns[:3]) == ["Barcode", "Genotype", "Replicate"]
    pc_cols = [c for c in scores.columns if c.startswith("PC")]
    assert pc_cols, "expected PC score columns"
    assert (
        list(scores.columns[-len(pc_cols) :]) == pc_cols
    )  # PCs are the trailing block
    assert "Computation.Time.s" in scores.columns  # carried as metadata, not a trait
    # Row-aligned with the cleaned frame's samples (same Barcodes, same order).
    final = _final_df()
    assert scores["Barcode"].tolist() == final["Barcode"].tolist()
    assert len(scores) == len(final) == 153


# 9.5 — the serialized PCAResult stamps the threshold that produced it (random_state None)


def test_persisted_result_records_threshold(injected_ports, monkeypatch):
    _reader, store = injected_ports
    captured = _capture_staged_outputs(store, monkeypatch)
    _run(explained_variance_threshold=0.8)

    payload = json.loads(captured["pca_result.json"])
    assert payload["explained_variance_threshold"] == pytest.approx(0.8)
    assert payload["random_state"] is None  # consistent with seed=None


# 9.8 — the consumed cleaned frame is content-addressed via source_csv


def test_passes_source_csv_for_input_lineage(injected_ports, monkeypatch):
    _reader, store = injected_ports
    captured: dict[str, object] = {}
    real_create = store.create_run

    def _spy(**kwargs):
        src = kwargs.get("source_csv")
        captured["source_csv"] = src
        captured["exists"] = src is not None and Path(src).exists()
        return real_create(**kwargs)

    monkeypatch.setattr(store, "create_run", _spy)
    _run()

    assert captured["source_csv"] is not None
    assert captured["exists"]  # the snapshot exists when the store hashes it


# 9.9 — snapshot is written index=False (regression guard for the snapshot_frame refactor)


def test_source_snapshot_written_index_false(injected_ports, monkeypatch):
    """The source snapshot CSV must NOT include the DataFrame index as a column.

    If ``to_csv(index=True)`` (the default) were used, every CSV would gain a
    spurious ``Unnamed: 0`` column that would corrupt content-addressing and
    confuse downstream readers.
    """
    _reader, store = injected_ports
    captured: dict[str, object] = {}
    real_create = store.create_run

    def _spy(**kwargs):
        src = kwargs.get("source_csv")
        if src is not None:
            captured["columns"] = list(pd.read_csv(src, encoding="utf-8").columns)
        return real_create(**kwargs)

    monkeypatch.setattr(store, "create_run", _spy)
    _run()

    assert "columns" in captured
    assert "Unnamed: 0" not in captured["columns"]


# ── 10. Plot generation (#426) ───────────────────────────────────────────────

_ALL_PLOT_KEYS = {
    "create_pca_scree_plot",
    "create_pca_biplot",
    "create_feature_contribution_plot",
    "create_feature_contribution_heatmap",
}


def _capture_staged_bytes(store, monkeypatch) -> dict[str, bytes]:
    """Read each staged PNG artifact's raw bytes at commit time."""
    captured: dict[str, bytes] = {}
    real_commit = store.commit

    def _commit(run, outputs):
        for name in outputs:
            p = run.staging_dir / name
            if p.suffix == ".png":
                captured[name] = p.read_bytes()
        return real_commit(run, outputs)

    monkeypatch.setattr(store, "commit", _commit)
    return captured


def test_default_no_plots_outputs_unchanged(injected_ports):
    """10.1 — no include_plots → outputs unchanged from pre-plots behavior."""
    result = _run()
    assert set(result.outputs) == {"loadings.csv", "scores.csv", "pca_result.json"}
    assert not any(k.endswith(".png") for k in result.outputs)


def test_unknown_plot_key_invalid_input_no_run_committed(injected_ports):
    """10.2 — unknown key → invalid_input, no run committed."""
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(include_plots=True, plots=["not_a_real_plot"])
    assert exc.value.code == "invalid_input"
    assert "not_a_real_plot" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "pca") == []


def test_duplicate_plot_key_invalid_input_no_run_committed(injected_ports):
    """10.3 — duplicate key → invalid_input naming the duplicate, no run."""
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(
            include_plots=True,
            plots=["create_pca_scree_plot", "create_pca_scree_plot"],
        )
    assert exc.value.code == "invalid_input"
    assert "create_pca_scree_plot" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "pca") == []


def test_empty_plots_list_is_invalid_input(injected_ports):
    """10.4 — plots=[] → invalid_input (use None for all)."""
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(include_plots=True, plots=[])
    assert exc.value.code == "invalid_input"
    assert store.list_runs(_EXPERIMENT, "pca") == []


def test_include_plots_false_with_plots_param_is_silently_ignored(injected_ports):
    """10.5 — include_plots=False + plots=[...] → no error, no PNG outputs."""
    _reader, store = injected_ports
    result = _run(include_plots=False, plots=["create_pca_scree_plot"])
    assert not any(k.endswith(".png") for k in result.outputs)
    assert set(result.outputs) == {"loadings.csv", "scores.csv", "pca_result.json"}


def test_all_four_plots_png_round_trip(injected_ports, monkeypatch):
    """10.6 — include_plots=True, plots=None → four PNGs with valid magic bytes."""
    _reader, store = injected_ports
    staged = _capture_staged_bytes(store, monkeypatch)
    result = _run(include_plots=True, plots=None)

    png_keys = {k for k in result.outputs if k.endswith(".png")}
    assert png_keys == {f"{k}.png" for k in _ALL_PLOT_KEYS}
    for key in png_keys:
        assert key in staged, f"staged bytes missing for {key}"
        assert staged[key][:4] == b"\x89PNG", f"{key} is not a valid PNG"


def test_plots_subset_generates_only_requested(injected_ports, monkeypatch):
    """10.7 — plots subset → only those PNGs in outputs."""
    _reader, store = injected_ports
    requested = ["create_pca_scree_plot", "create_pca_biplot"]
    staged = _capture_staged_bytes(store, monkeypatch)
    result = _run(include_plots=True, plots=requested)

    png_keys = {k for k in result.outputs if k.endswith(".png")}
    assert png_keys == {f"{k}.png" for k in requested}
    assert (
        set(result.outputs)
        == {
            "loadings.csv",
            "scores.csv",
            "pca_result.json",
        }
        | png_keys
    )
    for key in png_keys:
        assert staged[key][:4] == b"\x89PNG"


def test_figure_cleanup_get_fignums_empty_on_success(injected_ports):
    """10.8a — after a successful plots call, no figures remain open."""
    import matplotlib.pyplot as plt

    _run(include_plots=True, plots=None)
    assert plt.get_fignums() == []


def test_figure_cleanup_get_fignums_empty_on_invalid_key(injected_ports):
    """10.8b — after an invalid_key error, no figures remain open."""
    import matplotlib.pyplot as plt

    with pytest.raises(BloomMCPError):
        _run(include_plots=True, plots=["bad_key"])
    assert plt.get_fignums() == []


def test_figure_cleanup_get_fignums_empty_on_partial_plotter_failure(
    injected_ports, monkeypatch
):
    """10.8c — regression: the SECOND of several requested plotters raising mid-generation
    must not leak the figure(s) already produced by earlier successful plotters. Exercises
    the tool's real try/finally nesting end-to-end (not just the _plots unit helpers).
    """
    import matplotlib.pyplot as plt

    real = pca_analysis_tool._pca_plot_calls

    def _boom(*a, **k):
        raise RuntimeError("second plotter blew up")

    def _patched(result_dict, pca, frame, threshold):
        calls = real(result_dict, pca, frame, threshold)
        calls["create_pca_biplot"] = _boom
        return calls

    monkeypatch.setattr(pca_analysis_tool, "_pca_plot_calls", _patched)

    # as_mcp_tool maps the plotter's raw RuntimeError to BloomMCPError(internal_error);
    # what matters here is that no figure leaks past the finally, not the wrapped code.
    with pytest.raises(BloomMCPError):
        _run(
            include_plots=True,
            plots=["create_pca_scree_plot", "create_pca_biplot"],
        )
    assert plt.get_fignums() == []


def test_matplotlib_not_imported_on_default_path(injected_ports, monkeypatch):
    """10.9 — no include_plots → matplotlib import never reached."""
    import sys

    monkeypatch.setitem(sys.modules, "matplotlib", None)
    _run()  # must not raise ImportError


def test_plot_outputs_included_in_schema_round_trip(injected_ports):
    """10.10 — PNG keys survive model_dump_json / model_validate round-trip."""
    result = _run(include_plots=True, plots=["create_pca_scree_plot"])
    again = PCAAnalysisResult.model_validate(json.loads(result.model_dump_json()))
    assert "create_pca_scree_plot.png" in again.outputs


# ── explicit cleaned-version selector (#626) ────────────────────────────────


def test_version_field_exists():
    assert "version" in PCAAnalysisParams.model_fields


def test_omitting_version_preserves_todays_exact_call(injected_ports):
    reader, _store = injected_ports
    reader.load_experiment = MagicMock(wraps=reader.load_experiment)

    _run()

    reader.load_experiment.assert_called_once_with(_EXPERIMENT, require_clean=True)


def test_explicit_version_is_passed_through(injected_ports):
    reader, _store = injected_ports
    reader.add_cleaned_version(_EXPERIMENT, "v2", _final_df(), make_latest=False)
    reader.load_experiment = MagicMock(wraps=reader.load_experiment)

    _run(version="v2")

    reader.load_experiment.assert_called_once_with(
        _EXPERIMENT, require_clean=True, version="v2"
    )
