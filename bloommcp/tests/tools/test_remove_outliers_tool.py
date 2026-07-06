"""Contract + golden tests for the granular ``remove_outliers`` tool (#378).

Five contract patterns + the characterization golden through the MCP tool, plus the
remove_outliers -> cleaned-version composition a downstream ``require_clean=True``
consumer (e.g. ``pca_analysis``) relies on. The tool delegates ALL detection/removal
to ``sleap_roots_analyze.remove_outlier_samples`` and persists a versioned run via the
``ResultStore`` port under tool class ``qc`` — no outlier logic in the MCP.

The golden is a *characterization* pin: turface_19's mahalanobis chi-squared fit is
poor, so the 8 flagged samples are a method+seed snapshot, not ground truth.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest
from sleap_roots_analyze import clean_traits_for_analysis

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.experiment_utils import detect_columns
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.tools import _ports, remove_outliers_tool
from bloom_mcp.tools.remove_outliers_tool import (
    RemoveOutliersParams,
    RemoveOutliersResult,
    remove_outliers,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_raw_data.csv"
_GOLDEN = json.loads((_FIXTURES / "turface_19_outlier_golden.json").read_text())

_EXPERIMENT = "turface_19.csv"


def _roles(det: dict) -> dict[str, str]:
    roles = {
        "barcode_col": det["sample_id_col"],
        "genotype_col": det["genotype_col"],
        "replicate_col": det["replicate_col"],
    }
    return {k: v for k, v in roles.items() if v is not None}


def _cleaned_df() -> pd.DataFrame:
    """The turface_19 frame cleaned at canonical defaults — the tool's input.

    Uses the tested upstream ``clean_traits_for_analysis`` (not the code under test)
    with the reader-detected roles/traits, exactly as ``qc_clean`` would produce it.
    """
    raw = pd.read_csv(_RAW)
    det = detect_columns(raw)
    cleaned, _kept, _log = clean_traits_for_analysis(
        raw, trait_cols=det["trait_cols"], **_roles(det)
    )
    return cleaned


@pytest.fixture
def injected_ports():
    """FakeReader serving a cleaned turface_19 version + FakeResultStore, via _ports."""
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_cleaned_version(_EXPERIMENT, "v1", _cleaned_df(), make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _run(**overrides) -> RemoveOutliersResult:
    params = {"experiment": _EXPERIMENT, **overrides}
    return remove_outliers(RemoveOutliersParams(**params))


# ── 2. Golden trim through the tool (characterization) ──────────────────────


def test_golden_trim_counts_and_barcodes_match_recorded_snapshot(injected_ports):
    """2.2 — mahalanobis@seed42 reproduces the recorded characterization golden.

    NB: turface_19's chi-squared fit is poor; these are a method+seed pin, not truth.
    """
    result = _run(method="mahalanobis", seed=42)

    assert result.n_input_samples == _GOLDEN["n_input_samples"] == 158
    assert result.n_outliers == _GOLDEN["n_outliers"] == 8
    assert result.n_output_samples == _GOLDEN["n_output_samples"] == 150
    assert sorted(result.outlier_barcodes) == _GOLDEN["outlier_barcodes"]


def test_persisted_trimmed_table_has_output_rows_and_no_nans(injected_ports):
    """2.3 — the persisted trimmed table has n_output_samples rows and no NaNs."""
    _reader, store = injected_ports
    result = _run()
    stored = store.get_run(_EXPERIMENT, "qc", "latest")
    assert stored.output_keys[remove_outliers_tool.CLEANED_CSV_NAME].endswith(
        "_cleaned.csv"
    )
    # n_outliers + n_output == n_input (arithmetic), and output < input (some trimmed).
    assert result.n_output_samples < result.n_input_samples


def test_goodness_of_fit_is_dict_with_fit_quality_and_optional_types(injected_ports):
    """2.4 — goodness_of_fit is the delegate's fit-report dict; steer on fit_quality."""
    result = _run(method="mahalanobis")
    assert isinstance(result.goodness_of_fit, dict)
    assert result.goodness_of_fit["fit_quality"] == "very_poor"
    assert result.threshold_type == "chi_squared"
    assert isinstance(result.threshold_value, float)


# ── 3.1 tools/list presence ─────────────────────────────────────────────────


def test_remove_outliers_appears_in_tools_list_and_workflow_preserved():
    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "remove_outliers" in tools
    assert tools["remove_outliers"].inputSchema is not None
    assert "run_outlier_workflow" in tools  # additive — workflow not removed


# ── 3.2 schema round-trip ───────────────────────────────────────────────────


def test_valid_input_output_round_trip(injected_ports):
    result = _run()
    again = RemoveOutliersResult.model_validate(json.loads(result.model_dump_json()))
    assert again.n_output_samples == result.n_output_samples


def test_invalid_threshold_is_input_validation_error(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        remove_outliers({"experiment": _EXPERIMENT, "chi2_percentile": 150.0})
    assert exc.value.code == "invalid_input"


def test_unknown_method_is_input_validation_error(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        remove_outliers({"experiment": _EXPERIMENT, "method": "banana"})
    assert exc.value.code == "invalid_input"


# ── 3.3 provenance (resolved seed) + links (not blobs) + report round-trip ──


def test_provenance_seed_recorded_and_links_returned(injected_ports):
    _reader, store = injected_ports
    result = _run(seed=42)

    stored = store.get_run(_EXPERIMENT, "qc", "latest")
    assert stored.tool == "remove_outliers"
    assert stored.seed == 42  # stochastic — resolved integer seed recorded
    assert set(stored.output_keys) == {"_cleaned.csv", "outlier_report.json"}

    # Result returns links (run ref + manifest + object keys), never the table.
    assert result.run_ref == stored.run_ref
    assert result.manifest_path == stored.manifest_path
    assert set(result.outputs) == {"_cleaned.csv", "outlier_report.json"}
    assert not hasattr(result, "df")
    dumped = result.model_dump()
    assert not any(
        isinstance(v, (list, dict)) and len(str(v)) > 5000 for v in dumped.values()
    )


def test_outlier_report_json_round_trips(fake_supabase_storage):
    """The persisted outlier_report.json is valid JSON carrying the report (guards a
    numpy-not-serializable regression). Uses the real store over the in-memory object
    store so the committed bytes can be read back."""
    reader = FakeReader()
    reader.add_cleaned_version(_EXPERIMENT, "v1", _cleaned_df(), make_latest=True)
    store = SupabaseResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        result = _run()
        report_key = result.outputs["outlier_report.json"]
        payload = json.loads(fake_supabase_storage.objects[report_key].decode("utf-8"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert payload["n_outliers"] == _GOLDEN["n_outliers"] == 8
    assert isinstance(payload["goodness_of_fit"], dict)


# ── 3.4 property / invariant ────────────────────────────────────────────────


def test_counts_and_removal_fraction_are_consistent(injected_ports):
    result = _run()
    assert 0 < result.n_output_samples <= result.n_input_samples
    assert result.n_outliers == result.n_input_samples - result.n_output_samples
    assert result.removal_fraction == round(
        result.n_outliers / result.n_input_samples, 6
    )


# ── 3.5 delegation pinning (spy) + 3.5b default method ──────────────────────


def test_delegates_once_forwards_roles_seed_and_never_calls_vendored(
    injected_ports, monkeypatch
):
    captured = {}
    real = remove_outliers_tool.remove_outlier_samples

    def _spy(df, trait_cols=None, **kwargs):
        captured["n_calls"] = captured.get("n_calls", 0) + 1
        captured["kwargs"] = kwargs
        return real(df, trait_cols, **kwargs)

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _spy)

    import bloom_mcp.outlier_detection as vendored

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError(
            "remove_outliers called the vendored bloom_mcp.outlier_detection"
        )

    monkeypatch.setattr(vendored, "detect_outliers_mahalanobis", _boom)

    _run(method="mahalanobis", seed=42)

    assert captured["n_calls"] == 1
    assert captured["kwargs"]["method"] == "mahalanobis"
    assert captured["kwargs"]["random_state"] == 42  # forwarded by keyword
    assert captured["kwargs"]["barcode_col"] == "Barcode"
    assert captured["kwargs"]["genotype_col"] == "geno"
    assert captured["kwargs"]["replicate_col"] == "rep"


def test_default_method_is_mahalanobis(injected_ports, monkeypatch):
    captured = {}
    real = remove_outliers_tool.remove_outlier_samples

    def _spy(df, trait_cols=None, **kwargs):
        captured["kwargs"] = kwargs
        return real(df, trait_cols, **kwargs)

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _spy)
    _run()  # no method
    assert captured["kwargs"]["method"] == "mahalanobis"


# ── 3.6 role-column fallback (None must not be forwarded) + non-default roles ─


def _clean_synthetic(cols: list[str], n: int = 40) -> pd.DataFrame:
    # A tiny NaN-free frame with two well-separated trait clusters (no degenerate trim).
    rng = list(range(n))
    data = {c: [float(i % 7) + 0.1 * i for i in rng] for c in cols}
    return pd.DataFrame(data)


def test_undetected_roles_fall_back_to_delegate_defaults(monkeypatch):
    df = _clean_synthetic(["t1", "t2", "t3"])
    reader = FakeReader()
    reader.add_cleaned_version("roleless.csv", "v1", df, make_latest=True)
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)

    captured = {}

    def _spy(frame_df, trait_cols=None, **kwargs):
        captured["kwargs"] = kwargs
        keep = frame_df.iloc[:-1].copy()
        return keep, {
            "method": kwargs.get("method"),
            "n_input_samples": len(frame_df),
            "n_outliers": 1,
            "n_output_samples": len(keep),
            "removal_fraction": 1 / len(frame_df),
            "outlier_barcodes": [],
            "threshold_type": None,
            "threshold_value": None,
            "goodness_of_fit": None,
        }

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _spy)
    try:
        remove_outliers(RemoveOutliersParams(experiment="roleless.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    for role in ("genotype_col", "replicate_col", "barcode_col"):
        assert (
            captured["kwargs"].get(role) is not None or role not in captured["kwargs"]
        )


def test_non_default_roles_are_forwarded_overriding_delegate_defaults(monkeypatch):
    df = pd.DataFrame(
        {
            "Genotype": (["g1", "g2"] * 20),
            "Replicate": list(range(40)),
            "tA": [float(i % 5) for i in range(40)],
            "tB": [float((i % 3) + i * 0.1) for i in range(40)],
        }
    )
    reader = FakeReader()
    reader.add_cleaned_version("caps.csv", "v1", df, make_latest=True)
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)

    captured = {}

    def _spy(frame_df, trait_cols=None, **kwargs):
        captured["kwargs"] = kwargs
        keep = frame_df.iloc[:-1].copy()
        return keep, {
            "method": kwargs.get("method"),
            "n_input_samples": len(frame_df),
            "n_outliers": 1,
            "n_output_samples": len(keep),
            "removal_fraction": 1 / len(frame_df),
            "outlier_barcodes": [],
            "threshold_type": None,
            "threshold_value": None,
            "goodness_of_fit": None,
        }

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _spy)
    try:
        remove_outliers(RemoveOutliersParams(experiment="caps.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert captured["kwargs"]["genotype_col"] == "Genotype"
    assert captured["kwargs"]["replicate_col"] == "Replicate"


# ── 3.7 guardrail: un-cleaned input ─────────────────────────────────────────


def test_uncleaned_input_is_assumption_violated_run_qc_first():
    reader = FakeReader()
    reader.add_experiment("raw_only.csv", pd.read_csv(_RAW))  # raw only, no cleaned
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            remove_outliers(RemoveOutliersParams(experiment="raw_only.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert exc.value.code == "assumption_violated"
    assert "qc_clean" in exc.value.remedy
    assert store.list_runs("raw_only.csv", "qc") == []


# ── 3.8 degenerate trim (real delegate) + non-unique index ──────────────────


def test_overaggressive_trim_real_delegate_is_assumption_violated(injected_ports):
    """A low chi2_percentile trims below the minimum survivors — the real delegate
    RAISES; it must surface as self-correctable assumption_violated, not internal_error.
    """
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(method="mahalanobis", chi2_percentile=0.0001)
    assert exc.value.code == "assumption_violated"
    assert store.list_runs(_EXPERIMENT, "qc") == []


def test_non_unique_index_is_structured_error(monkeypatch):
    cleaned = _cleaned_df()
    cleaned.index = [0] * len(cleaned)  # non-unique index
    reader = FakeReader()
    reader.add_cleaned_version("dup.csv", "v1", cleaned, make_latest=True)
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            remove_outliers(RemoveOutliersParams(experiment="dup.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert exc.value.code == "assumption_violated"
    assert store.list_runs("dup.csv", "qc") == []


# ── 3.8b leak scrub ─────────────────────────────────────────────────────────


def test_undeclared_delegate_raise_is_scrubbed(injected_ports, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "internal_error"
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "secret" not in msg and "/var" not in msg and "db.internal" not in msg


# ── 3.9 method-surface validation + isolation_forest happy path ─────────────


def test_contamination_with_mahalanobis_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(method="mahalanobis", contamination=0.05)
    assert exc.value.code == "invalid_input"
    assert "contamination" in exc.value.message


def test_chi2_percentile_with_isolation_forest_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(method="isolation_forest", chi2_percentile=97.5)
    assert exc.value.code == "invalid_input"
    assert "chi2_percentile" in exc.value.message


def test_isolation_forest_happy_path_has_null_threshold_and_fit(injected_ports):
    result = _run(method="isolation_forest")
    assert result.threshold_type is None
    assert result.threshold_value is None
    assert result.goodness_of_fit is None
    assert result.n_output_samples > 0


# ── 3.10 composition: trimmed run resolves via require_clean ─────────────────


def test_trimmed_run_composes_into_require_clean_read(fake_supabase_storage):
    """A committed remove_outliers run is resolvable by require_clean=True (the path
    a downstream pca_analysis relies on). Driven through the Supabase adapters over the
    shared in-memory object store — the fakes' reader/store are disjoint."""
    reader = FakeReader()
    reader.add_cleaned_version(_EXPERIMENT, "v1", _cleaned_df(), make_latest=True)
    store = SupabaseResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        result = _run()
        resolved = SupabaseReader().load_experiment(_EXPERIMENT, require_clean=True)
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert resolved.source.endswith("_cleaned")
    assert resolved.source != "raw"
    assert int(resolved.df[resolved.trait_cols].isna().sum().sum()) == 0
    # The reloaded trimmed table has the golden output rows — a persisted-NaN/wrong-rows
    # regression fails here (the FakeResultStore path can't reload).
    assert len(resolved.df) == result.n_output_samples == _GOLDEN["n_output_samples"]


# ── 3.11 plots ──────────────────────────────────────────────────────────────


def test_default_is_report_only_no_plots(injected_ports):
    _reader, store = injected_ports
    result = _run()
    assert set(result.outputs) == {"_cleaned.csv", "outlier_report.json"}


def test_include_plots_persists_requested_figure_as_link(injected_ports):
    result = _run(include_plots=True, plots=["mahalanobis_pc_analysis"])
    assert "mahalanobis_pc_analysis.png" in result.outputs
    assert "_cleaned.csv" in result.outputs
    # figures are links (object keys), not inline blobs
    assert isinstance(result.outputs["mahalanobis_pc_analysis.png"], str)


def test_unknown_plot_key_is_invalid_input_with_no_run(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(include_plots=True, plots=["not_a_real_figure"])
    assert exc.value.code == "invalid_input"
    assert "not_a_real_figure" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "qc") == []


# ── 3.12 second run increments version and supersedes latest ────────────────


def test_second_run_increments_version_and_supersedes_latest(injected_ports):
    _reader, store = injected_ports
    _run()
    _run()
    assert [r.run_ref for r in store.list_runs(_EXPERIMENT, "qc")] == ["v1", "v2"]
    assert store.get_run(_EXPERIMENT, "qc", "latest").run_ref == "v2"
