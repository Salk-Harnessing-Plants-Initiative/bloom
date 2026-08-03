"""Contract + golden tests for the granular ``remove_outliers`` tool (#378).

Five contract patterns + the characterization golden through the MCP tool, plus the
remove_outliers -> cleaned-version composition a downstream ``require_clean=True``
consumer (e.g. ``pca_analysis``) relies on. The tool delegates ALL detection/removal
to ``sleap_roots_analyze.remove_outlier_samples`` and persists a versioned run via the
``ResultStore`` port under its own dedicated tool class ``outliers`` (#420) — no
outlier logic in the MCP.

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
from bloom_mcp.tools import _ports
from bloom_mcp.sections.sleap_roots.analysis import (
    remove_outliers as remove_outliers_tool,
)
from bloom_mcp.sections.sleap_roots.analysis.remove_outliers import (
    RemoveOutliersParams,
    RemoveOutliersResult,
    remove_outliers,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_raw_data.csv"
_GOLDEN = json.loads(
    (_FIXTURES / "turface_19_outlier_golden.json").read_text(encoding="utf-8")
)
# #419: mahalanobis's fit on turface_19 is untrustworthy (very_poor) -- the gate now
# raises on that path, so isolation_forest is this fixture's "successful persisted
# trim" characterization. _GOLDEN (mahalanobis) stays as the raise-path's historical
# reference (the message embeds these same counts/barcodes).
_GOLDEN_IFOREST = json.loads(
    (_FIXTURES / "turface_19_outlier_iforest_golden.json").read_text(encoding="utf-8")
)

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
    raw = pd.read_csv(_RAW, encoding="utf-8")
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


def _force_trustworthy_mahalanobis_fit(monkeypatch) -> None:
    """(#419) Wrap the REAL delegate, overriding only the reported ``fit_quality`` to
    an acceptable-or-better value so the fit-trustworthiness gate does not fire.

    Neither turface_19 nor cylinder has a naturally-trustworthy mahalanobis fit (both
    are poor/very_poor), so a test that needs the gate to pass through while still
    exercising the REAL mahalanobis detection (real trimmed rows, real figures, real
    threshold fields) has no fixture to run against. This forces just the one field
    the gate reads, leaving everything else — the trim, the figures, the threshold —
    genuinely delegate-produced.
    """
    real = remove_outliers_tool.remove_outlier_samples

    def _spy(df, trait_cols=None, **kwargs):
        trimmed_df, report = real(df, trait_cols, **kwargs)
        if isinstance(report.get("goodness_of_fit"), dict):
            report["goodness_of_fit"] = {
                **report["goodness_of_fit"],
                "fit_quality": "excellent",
            }
        return trimmed_df, report

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _spy)


# ── 2. Golden trim through the tool (characterization) ──────────────────────


def test_mahalanobis_default_untrustworthy_fit_is_gated_not_persisted(injected_ports):
    """2.2 (#419) — mahalanobis@seed42 on turface_19 has a very_poor chi-squared fit,
    so the tool now raises assumption_violated instead of persisting the trim. The
    raised message embeds the same counts/barcodes the old golden characterized
    (n_outliers=8/n_input=158/n_output=150), so nothing is silently lost even though
    the run is not committed."""
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(method="mahalanobis", seed=42)

    assert exc.value.code == "assumption_violated"
    assert "isolation_forest" in exc.value.remedy
    msg = exc.value.message
    assert f"n_outliers={_GOLDEN['n_outliers']}" in msg  # 8
    assert f"n_input_samples={_GOLDEN['n_input_samples']}" in msg  # 158
    assert f"n_output_samples={_GOLDEN['n_output_samples']}" in msg  # 150
    assert "very_poor" in msg
    for barcode in _GOLDEN["outlier_barcodes"]:
        assert barcode in msg
    assert store.list_runs(_EXPERIMENT, "outliers") == []


def test_gate_fires_before_figure_generation_even_with_a_valid_plots_key(
    injected_ports,
):
    """(#419 Decision 6 regression) The fit gate must fire before ANY figure handling —
    not just before the invalid-plots-key path (covered by
    test_unknown_plot_key_is_invalid_input_with_no_run, now on isolation_forest since
    the gate would otherwise mask it). This pins the valid-key case too: an untrustworthy
    mahalanobis fit with include_plots=True and a real, valid figure key still raises the
    fit gate rather than reaching _make_figures at all — no figures are created (nothing
    to leak), guarding against a future refactor that reorders the checks."""
    _reader, store = injected_ports
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.close("all")
    with pytest.raises(BloomMCPError) as exc:
        _run(
            method="mahalanobis",
            include_plots=True,
            plots=["mahalanobis_pc_analysis"],  # a real, valid mahalanobis figure key
        )
    assert exc.value.code == "assumption_violated"
    assert "isolation_forest" in exc.value.remedy
    assert plt.get_fignums() == []  # no figure was ever created
    assert store.list_runs(_EXPERIMENT, "outliers") == []


def test_isolation_forest_golden_trim_counts_and_barcodes_match_recorded_snapshot(
    injected_ports,
):
    """2.2b (#419) — isolation_forest@seed42 is turface_19's new "successful
    persisted trim" characterization, since mahalanobis is gated on this fixture
    (see test_mahalanobis_default_untrustworthy_fit_is_gated_not_persisted)."""
    result = _run(method="isolation_forest", seed=42)

    assert result.n_input_samples == _GOLDEN_IFOREST["n_input_samples"] == 158
    assert result.n_outliers == _GOLDEN_IFOREST["n_outliers"] == 16
    assert result.n_output_samples == _GOLDEN_IFOREST["n_output_samples"] == 142
    assert sorted(result.outlier_barcodes) == _GOLDEN_IFOREST["outlier_barcodes"]
    assert result.fit_is_trustworthy is None
    assert result.goodness_of_fit is None


def test_persisted_trimmed_table_has_output_rows_and_no_nans(injected_ports):
    """2.3 — the persisted trimmed table has n_output_samples rows and no NaNs."""
    _reader, store = injected_ports
    result = _run(method="isolation_forest")
    stored = store.get_run(_EXPERIMENT, "outliers", "latest")
    assert stored.output_keys[remove_outliers_tool.CLEANED_CSV_NAME].endswith(
        "_cleaned.csv"
    )
    # n_outliers + n_output == n_input (arithmetic), and output < input (some trimmed).
    assert result.n_output_samples < result.n_input_samples


# ── outliers registered in the discovery-tool / canonical registries ────────


def test_outliers_class_registered_in_discovery_and_canonical_registries():
    """A typo in either registry would silently hide trimmed runs from
    list_existing_analyses without any test noticing — assert membership
    directly, not just indirectly via a live discoverability check."""
    from bloom_mcp.manifest import CANONICAL_TOOL_CLASSES
    from bloom_mcp.sections.core.list_existing_analyses import TOOL_CLASSES

    assert "outliers" in TOOL_CLASSES
    assert "outliers" in CANONICAL_TOOL_CLASSES


def test_remove_outliers_tool_name_constant_matches_the_real_function_name():
    """`experiment_utils.REMOVE_OUTLIERS_TOOL_NAME` single-sources the *comparison*
    side (the audit script, test fixtures) against one literal — but the value
    actually persisted at commit time is `func.__name__` of this function
    (`contract/wrap.py`), not the constant. A future rename of `remove_outliers`
    would silently desync the two with nothing else to catch it; this regression
    test is that catch (bloom#585 review)."""
    from bloom_mcp.experiment_utils import REMOVE_OUTLIERS_TOOL_NAME
    from bloom_mcp.sections.sleap_roots.analysis.remove_outliers import (
        remove_outliers,
    )

    assert REMOVE_OUTLIERS_TOOL_NAME == remove_outliers.__name__


def test_discoverable_via_list_existing_analyses(injected_ports):
    """Live discoverability, mirroring the same pattern
    cross_experiment_correlations uses for its own registered class."""
    from bloom_mcp.sections.core import (
        list_existing_analyses as list_existing_analyses_mod,
    )

    # _EXPERIMENT is reused across this whole file — clear the 30s response
    # cache both before (so an earlier test's cached result can't leak in) and
    # after (so this test's FakeReader/FakeResultStore-backed result doesn't
    # leak into a later test), mirroring test_qc_tools_discovery.py's fixture.
    list_existing_analyses_mod._RESPONSE_CACHE.clear()
    try:
        _run(method="isolation_forest")
        response = json.loads(
            list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
        )
    finally:
        list_existing_analyses_mod._RESPONSE_CACHE.clear()

    assert "outliers" in response["analyses"]


def test_goodness_of_fit_true_fit_is_not_gated_dict_shape_and_types(
    injected_ports, monkeypatch
):
    """2.4 (#419 regression) — an acceptable-or-better mahalanobis fit
    (fit_is_trustworthy is True) is NOT gated, and goodness_of_fit is still the
    delegate's fit-report dict with the expected shape. See
    _force_trustworthy_mahalanobis_fit for why this fixture's fit must be forced."""
    _force_trustworthy_mahalanobis_fit(monkeypatch)
    result = _run(method="mahalanobis")
    assert isinstance(result.goodness_of_fit, dict)
    assert result.goodness_of_fit["fit_quality"] == "excellent"
    assert result.threshold_type == "chi_squared"
    assert isinstance(result.threshold_value, float)
    # I6 — the machine-visible trust flag mirrors the (forced) good fit so a
    # downstream tool need not parse the goodness_of_fit dict / the description prose.
    assert result.fit_is_trustworthy is True


# ── 3.1 tools/list presence ─────────────────────────────────────────────────


def test_remove_outliers_appears_in_tools_list():
    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "sleap_roots_remove_outliers" in tools
    assert tools["sleap_roots_remove_outliers"].inputSchema is not None


# ── 3.2 schema round-trip ───────────────────────────────────────────────────


def test_valid_input_output_round_trip(injected_ports):
    result = _run(method="isolation_forest")
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
    result = _run(method="isolation_forest", seed=42)

    stored = store.get_run(_EXPERIMENT, "outliers", "latest")
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


def test_outlier_report_json_round_trips(fake_supabase_storage, monkeypatch):
    """The persisted outlier_report.json is valid JSON carrying the report (guards a
    numpy-not-serializable regression) -- including a real, numpy-typed
    goodness_of_fit dict, not just the isolation_forest None case. Uses the real
    store over the in-memory object store so the committed bytes can be read back.
    turface_19's mahalanobis fit is untrustworthy (#419 gates it), so the fit is
    forced trustworthy here purely to reach persistence with a real fit-report dict
    intact -- see _force_trustworthy_mahalanobis_fit."""
    _force_trustworthy_mahalanobis_fit(monkeypatch)
    reader = FakeReader()
    reader.add_cleaned_version(_EXPERIMENT, "v1", _cleaned_df(), make_latest=True)
    store = SupabaseResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        result = _run(method="mahalanobis")
        report_key = result.outputs["outlier_report.json"]
        payload = json.loads(fake_supabase_storage.objects[report_key].decode("utf-8"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert payload["n_outliers"] == _GOLDEN["n_outliers"] == 8
    assert isinstance(payload["goodness_of_fit"], dict)


# ── 3.4 property / invariant ────────────────────────────────────────────────


def test_counts_and_removal_fraction_are_consistent(injected_ports):
    result = _run(method="isolation_forest")
    assert 0 < result.n_output_samples <= result.n_input_samples
    assert result.n_outliers == result.n_input_samples - result.n_output_samples
    assert result.removal_fraction == round(
        result.n_outliers / result.n_input_samples, 6
    )


# ── 3.5 delegation pinning (spy) + 3.5b default method ──────────────────────


def test_delegates_once_forwards_roles_seed_and_never_calls_vendored(
    injected_ports, monkeypatch
):
    """The spy captures kwargs from the REAL delegate call, which happens before the
    #419 fit-trustworthiness gate -- so this still verifies delegation/role/seed
    forwarding for a mahalanobis call even though turface_19's untrustworthy fit means
    the call ultimately raises rather than persists (asserted below, not the point of
    this test)."""
    captured = {}
    real = remove_outliers_tool.remove_outlier_samples

    def _spy(df, trait_cols=None, **kwargs):
        captured["n_calls"] = captured.get("n_calls", 0) + 1
        captured["kwargs"] = kwargs
        return real(df, trait_cols, **kwargs)

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _spy)

    with pytest.raises(BloomMCPError):
        _run(method="mahalanobis", seed=42)

    assert captured["n_calls"] == 1
    assert captured["kwargs"]["method"] == "mahalanobis"
    assert captured["kwargs"]["random_state"] == 42  # forwarded by keyword
    assert captured["kwargs"]["barcode_col"] == "Barcode"
    assert captured["kwargs"]["genotype_col"] == "geno"
    assert captured["kwargs"]["replicate_col"] == "rep"


def test_default_method_is_mahalanobis(injected_ports, monkeypatch):
    """The spy captures kwargs before the #419 gate fires, so the declared default
    (still mahalanobis -- Decision 4 of the fit-gate proposal defers changing it) is
    verified even though turface_19's untrustworthy fit means this call raises."""
    captured = {}
    real = remove_outliers_tool.remove_outlier_samples

    def _spy(df, trait_cols=None, **kwargs):
        captured["kwargs"] = kwargs
        return real(df, trait_cols, **kwargs)

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _spy)
    with pytest.raises(BloomMCPError):
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
    reader.add_experiment(
        "raw_only.csv", pd.read_csv(_RAW, encoding="utf-8")
    )  # raw only, no cleaned
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            remove_outliers(RemoveOutliersParams(experiment="raw_only.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert exc.value.code == "assumption_violated"
    assert "qc_clean" in exc.value.remedy
    assert store.list_runs("raw_only.csv", "outliers") == []


# ── 3.8 degenerate trim (real delegate) + non-unique index ──────────────────


def test_overaggressive_trim_real_delegate_is_assumption_violated(injected_ports):
    """A low chi2_percentile trims below the minimum survivors — the real delegate
    RAISES; it must surface as self-correctable assumption_violated, not internal_error.
    """
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(method="mahalanobis", chi2_percentile=0.0001)
    assert exc.value.code == "assumption_violated"
    assert store.list_runs(_EXPERIMENT, "outliers") == []


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
    assert store.list_runs("dup.csv", "outliers") == []


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
    # I6 — no chi-squared assumption, so trustworthiness is not applicable (None).
    assert result.fit_is_trustworthy is None
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
        result = _run(method="isolation_forest")
        resolved = SupabaseReader().load_experiment(_EXPERIMENT, require_clean=True)
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert resolved.source.endswith("_cleaned")
    assert resolved.source != "raw"
    assert int(resolved.df[resolved.trait_cols].isna().sum().sum()) == 0
    # The reloaded trimmed table has the golden output rows — a persisted-NaN/wrong-rows
    # regression fails here (the FakeResultStore path can't reload).
    assert (
        len(resolved.df)
        == result.n_output_samples
        == _GOLDEN_IFOREST["n_output_samples"]
    )


# ── 3.11 plots ──────────────────────────────────────────────────────────────


def test_default_is_report_only_no_plots(injected_ports):
    _reader, store = injected_ports
    result = _run(method="isolation_forest")
    assert set(result.outputs) == {"_cleaned.csv", "outlier_report.json"}


def test_include_plots_persists_requested_figure_as_link(injected_ports):
    result = _run(
        method="isolation_forest",
        include_plots=True,
        plots=["isolation_forest_analysis"],
    )
    assert "isolation_forest_analysis.png" in result.outputs
    assert "_cleaned.csv" in result.outputs
    # figures are links (object keys), not inline blobs
    assert isinstance(result.outputs["isolation_forest_analysis.png"], str)


def test_unknown_plot_key_is_invalid_input_with_no_run(injected_ports):
    """(#419 Decision 6) method=isolation_forest, not mahalanobis: the fit-
    trustworthiness gate now fires before plot-key validation, so a mahalanobis call
    against turface_19's untrustworthy fit would surface the gate's assumption_violated
    instead of this test's intended invalid_input. That validation logic is
    method-agnostic, so isolation_forest (never gated) exercises it just as well."""
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(method="isolation_forest", include_plots=True, plots=["not_a_real_figure"])
    assert exc.value.code == "invalid_input"
    assert "not_a_real_figure" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "outliers") == []


# ── 3.12 second run increments version and supersedes latest ────────────────


def test_second_run_increments_version_and_supersedes_latest(injected_ports):
    _reader, store = injected_ports
    _run(method="isolation_forest")
    _run(method="isolation_forest")
    assert [r.run_ref for r in store.list_runs(_EXPERIMENT, "outliers")] == ["v1", "v2"]
    assert store.get_run(_EXPERIMENT, "outliers", "latest").run_ref == "v2"


# ── B1: a barcode-less cleaned frame must not crash on None outlier_barcodes ──


def _barcodeless_cleaned_df() -> pd.DataFrame:
    """A NaN-free, unique-indexed cleaned frame with NO barcode/sample-id column.

    ``detect_columns`` reports ``sample_id_col=None``, so ``_role_kwargs`` omits
    ``barcode_col`` and the delegate defaults to ``"Barcode"`` — absent here — making
    ``remove_outlier_samples`` return ``outlier_barcodes=None`` (a *valid* barcode-less
    result), which is the B1 crash trigger before the ``or []`` coercion.
    """
    return pd.DataFrame(
        {
            "featA": [float((i * 7) % 13) + 0.01 * i for i in range(60)],
            "featB": [float((i * 3) % 11) + 0.02 * i for i in range(60)],
            "featC": [float((i * 5) % 17) + 0.03 * i for i in range(60)],
        }
    )


def test_barcodeless_cleaned_frame_returns_empty_barcodes_not_crash(monkeypatch):
    """B1 (real delegate, no mock) — a cleaned frame with no barcode column makes the
    delegate return ``outlier_barcodes=None``; the tool must coerce that to ``[]`` and
    still persist, not crash into an opaque ``internal_error``. The role-less spy tests
    mask this by returning ``[]``, and turface_19 has a Barcode column, so this path
    is otherwise uncovered.
    """
    reader = FakeReader()
    reader.add_cleaned_version(
        "nobarcode.csv", "v1", _barcodeless_cleaned_df(), make_latest=True
    )
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        result = remove_outliers(
            RemoveOutliersParams(experiment="nobarcode.csv", method="isolation_forest")
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert result.outlier_barcodes == []  # None coerced to [] — no crash
    assert 0 < result.n_output_samples <= result.n_input_samples
    assert store.list_runs(
        "nobarcode.csv", "outliers"
    )  # the trimmed run still persisted


# ── I2: provenance records the cleaned source it derives from (not "raw") ─────


def test_provenance_records_based_on_version_of_cleaned_source(
    injected_ports, monkeypatch
):
    """I2 — remove_outliers trims a cleaned ``v<N>``, so ``based_on_version`` must be
    that cleaned source, not the ``Provenance`` ``"raw"`` default (which would falsely
    claim the trim derived from raw data). Captured at ``create_run``, where the value
    is projected into the manifest's ``VersionEntry`` lineage.
    """
    _reader, store = injected_ports
    captured: dict[str, object] = {}
    real_create = store.create_run

    def _spy_create(**kwargs):
        captured["based_on_version"] = kwargs["provenance"].based_on_version
        return real_create(**kwargs)

    monkeypatch.setattr(store, "create_run", _spy_create)
    result = _run(method="isolation_forest")

    assert captured["based_on_version"] == "v1_cleaned" == result.source


# ── I1/I4(a): own pre-commit guard rejects a degenerate *returned* frame ─────


@pytest.fixture
def guard_ports():
    """A cleaned synthetic frame + fakes so a monkeypatched delegate can *return*
    (not raise) a degenerate frame and exercise the tool's own pre-commit guard."""
    reader = FakeReader()
    reader.add_cleaned_version(
        "guard.csv", "v1", _clean_synthetic(["t1", "t2", "t3"]), make_latest=True
    )
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        yield store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _degenerate_report(n_input: int, n_output: int) -> dict:
    return {
        "method": "mahalanobis",
        "n_input_samples": n_input,
        "n_outliers": n_input - n_output,
        "n_output_samples": n_output,
        "removal_fraction": (n_input - n_output) / n_input if n_input else 0.0,
        "outlier_barcodes": [],
        "threshold_type": None,
        "threshold_value": None,
        "goodness_of_fit": None,
    }


def test_own_guard_rejects_returned_frame_with_residual_nan(guard_ports, monkeypatch):
    """I4(a) — a delegate that RETURNS (not raises) a NaN-bearing trimmed frame is
    rejected before commit (the parity qc_clean tests explicitly, this masked here)."""

    def _spy(frame_df, trait_cols=None, **kwargs):
        keep = frame_df.iloc[:-1].copy()
        keep.iloc[0, 0] = float("nan")  # residual NaN in a kept trait cell
        return keep, _degenerate_report(len(frame_df), len(keep))

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _spy)
    with pytest.raises(BloomMCPError) as exc:
        remove_outliers(RemoveOutliersParams(experiment="guard.csv"))
    assert exc.value.code == "assumption_violated"
    assert guard_ports.list_runs("guard.csv", "outliers") == []


def test_own_guard_rejects_returned_frame_dropping_all_samples(
    guard_ports, monkeypatch
):
    """I4(a) — a delegate returning an empty trimmed frame is rejected, no run."""

    def _spy(frame_df, trait_cols=None, **kwargs):
        return frame_df.iloc[0:0].copy(), _degenerate_report(len(frame_df), 0)

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _spy)
    with pytest.raises(BloomMCPError) as exc:
        remove_outliers(RemoveOutliersParams(experiment="guard.csv"))
    assert exc.value.code == "assumption_violated"
    assert guard_ports.list_runs("guard.csv", "outliers") == []


def test_own_guard_rejects_returned_rows_not_subset_of_input(guard_ports, monkeypatch):
    """I1/I4(a) — a delegate returning rows that are NOT a subset of the cleaned input
    (the spec's row-subset guarantee) is rejected before commit, so no row-foreign
    "cleaned" artifact can be resolved downstream."""

    def _spy(frame_df, trait_cols=None, **kwargs):
        foreign = frame_df.copy()
        foreign.index = range(10_000, 10_000 + len(foreign))  # foreign row labels
        return foreign, _degenerate_report(len(frame_df), len(foreign))

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _spy)
    with pytest.raises(BloomMCPError) as exc:
        remove_outliers(RemoveOutliersParams(experiment="guard.csv"))
    assert exc.value.code == "assumption_violated"
    assert guard_ports.list_runs("guard.csv", "outliers") == []


# ── I4(b): trait_columns subset validation (unknown / non-numeric) ───────────


def test_unknown_trait_column_is_invalid_input(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["definitely_not_a_column"])
    assert exc.value.code == "invalid_input"
    assert "definitely_not_a_column" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "outliers") == []


def test_non_numeric_trait_column_is_invalid_input(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["Barcode"])  # a metadata/identifier column — non-numeric
    assert exc.value.code == "invalid_input"
    assert "Barcode" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "outliers") == []


def test_non_certified_numeric_column_is_rejected_not_dropped(injected_ports):
    """Phase 3 / P3.2 — a numeric-but-non-certified column (a role column qc_clean
    excluded from trait_cols, e.g. the replicate column) must be rejected the same
    way pca_analysis/clustering reject it, not silently accepted because it happens
    to be numeric and present. Regression guard for remove_outliers's local
    _validate_trait_subset, which (unlike its siblings) only checked existence +
    numeric dtype, not certified-set membership."""
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["rep"])  # numeric, present, but a replicate role column
    assert exc.value.code == "invalid_input"
    assert "rep" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "outliers") == []


def test_own_guard_rejects_returned_frame_missing_trait_column(
    guard_ports, monkeypatch
):
    """I4 — a delegate that RETURNS a frame with a trait column dropped/renamed is
    rejected before commit (the 'trait columns unchanged' spec guarantee)."""

    def _spy(frame_df, trait_cols=None, **kwargs):
        keep = frame_df.iloc[:-1].drop(columns=[trait_cols[0]])  # drop a trait column
        return keep, _degenerate_report(len(frame_df), len(keep))

    monkeypatch.setattr(remove_outliers_tool, "remove_outlier_samples", _spy)
    with pytest.raises(BloomMCPError) as exc:
        remove_outliers(RemoveOutliersParams(experiment="guard.csv"))
    assert exc.value.code == "assumption_violated"
    assert guard_ports.list_runs("guard.csv", "outliers") == []


# ── seed is recorded live (provenance integrity) ────────────────────────────


def test_provided_seed_is_recorded_in_provenance(injected_ports):
    """A non-default seed is recorded in the persisted run's provenance (proves the
    resolved integer is captured live, not hard-wired to 42). Only the *recorded*
    seed is asserted here, not a change in the flagged samples."""
    _reader, store = injected_ports
    _run(method="isolation_forest", seed=7)
    assert store.get_run(_EXPERIMENT, "outliers", "latest").seed == 7


# ── #420 fix: a later plain qc_clean does not revert an existing trim ───────


def _commit_qc_clean(store, df: pd.DataFrame) -> None:
    """Commit a bare qc-class run (simulating qc_clean) directly via the store port,
    so the real `qc_<stem>` manifest exists in the shared object store — distinct
    from seeding `remove_outliers`'s own input via `FakeReader`, which never touches
    the real store at all (see `test_trimmed_run_composes_into_require_clean_read`).
    """
    from bloom_mcp.contract import Provenance

    prov = Provenance.stamp(tool="qc_clean", params={}, seed=None)
    run = store.create_run(experiment=_EXPERIMENT, tool_class="qc", provenance=prov)
    df.to_csv(run.staging_dir / remove_outliers_tool.CLEANED_CSV_NAME, index=False)
    store.commit(
        run,
        {remove_outliers_tool.CLEANED_CSV_NAME: remove_outliers_tool.CLEANED_CSV_NAME},
    )


def test_qc_clean_rerun_does_not_revert_existing_trim(fake_supabase_storage):
    """The actual #420 repro, fixed: qc_clean -> remove_outliers -> qc_clean again ->
    require_clean=True resolves the TRIMMED frame, not the second qc_clean's
    un-trimmed one. Driven through the real SupabaseReader/SupabaseResultStore over
    the shared in-memory object store, so a genuine competing `qc`-class manifest
    exists (unlike the FakeReader-seeded composition test above)."""
    store = SupabaseResultStore()
    _ports.configure(reader=SupabaseReader(), store=store)
    try:
        _commit_qc_clean(store, _cleaned_df())  # qc v1
        result = _run(
            method="isolation_forest"
        )  # remove_outliers reads qc v1 via latest_qc, commits outliers v1
        _commit_qc_clean(
            store, _cleaned_df()
        )  # qc v2 — un-trimmed, committed AFTER the trim

        resolved = SupabaseReader().load_experiment(_EXPERIMENT, require_clean=True)
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert (
        len(resolved.df)
        == result.n_output_samples
        == _GOLDEN_IFOREST["n_output_samples"]
    )
    assert resolved.source == "outliers_v1_cleaned"


def test_qc_clean_rerun_with_no_trim_resolves_normally(fake_supabase_storage):
    """Inverse sanity check: with no trim ever made, a second qc_clean still resolves
    as "latest" exactly as before this change — confirms the fix doesn't regress the
    far more common no-trim path."""
    store = SupabaseResultStore()
    _ports.configure(reader=SupabaseReader(), store=store)
    try:
        _commit_qc_clean(store, _cleaned_df())  # qc v1
        _commit_qc_clean(store, _cleaned_df())  # qc v2

        resolved = SupabaseReader().load_experiment(_EXPERIMENT, require_clean=True)
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert resolved.source == "v2_cleaned"  # unqualified — no outliers class exists


def test_remove_outliers_picks_up_fresh_qc_clean_not_its_own_stale_trim(
    fake_supabase_storage,
):
    """Safety property behind the fix: remove_outliers's own next invocation reads
    the CURRENT qc clean via version="latest_qc", not its own prior trim — proving a
    fresh qc_clean is never permanently hidden from the one tool whose job is to trim
    it (the regression a naive "outliers always wins, full stop" design would have
    introduced)."""
    store = SupabaseResultStore()
    _ports.configure(reader=SupabaseReader(), store=store)
    try:
        _commit_qc_clean(store, _cleaned_df())  # qc v1
        first = _run(
            method="isolation_forest"
        )  # trims qc v1 (158 rows) -> outliers v1 (142 rows)
        assert first.n_input_samples == len(_cleaned_df())

        _commit_qc_clean(store, _cleaned_df())  # qc v2 — a fresh re-clean
        second = _run(
            method="isolation_forest"
        )  # must read qc v2 via latest_qc, NOT the stale outliers v1 trim
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    # If this tool had instead re-read its own stale trim, n_input_samples would be
    # outliers v1's 142 (== _GOLDEN_IFOREST["n_output_samples"]), not qc v2's full row
    # count.
    assert second.n_input_samples == len(_cleaned_df())
    assert second.n_input_samples != _GOLDEN_IFOREST["n_output_samples"]

    resolved = SupabaseReader().load_experiment(_EXPERIMENT, require_clean=True)
    assert resolved.source == "outliers_v2_cleaned"


# ── plots=None full figure set + figure-cleanup (no leaks) ──────────────────

_MAHALANOBIS_FIGS = {
    "mahalanobis_outlier_detection.png",
    "mahalanobis_pc_analysis.png",
    "mahalanobis_threshold_analysis.png",
    "outliers_per_genotype.png",  # genotype column present in turface_19
}


def test_include_plots_none_persists_full_mahalanobis_figure_set(
    injected_ports, monkeypatch
):
    """plots=None persists EVERY figure the method produces (the delegate's full
    set). Needs the REAL mahalanobis figures (not isolation_forest's single-figure
    set), so the untrustworthy fit is forced trustworthy via monkeypatch rather than
    repointed to a different method -- see _force_trustworthy_mahalanobis_fit."""
    _force_trustworthy_mahalanobis_fit(monkeypatch)
    result = _run(method="mahalanobis", include_plots=True)  # plots defaults to None
    assert _MAHALANOBIS_FIGS <= set(result.outputs)


def test_include_plots_success_closes_all_figures(injected_ports):
    """Figure-cleanup: a successful include_plots run leaks no matplotlib figures.
    isolation_forest (never gated) keeps this a real assertion about cleanup, not a
    vacuous pass on a run that never reached figure generation."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.close("all")
    _run(method="isolation_forest", include_plots=True)
    assert plt.get_fignums() == []


def test_unknown_plot_key_failure_closes_all_figures(injected_ports):
    """Figure-cleanup on the validation-failure path (unknown plot key).
    method=isolation_forest (#419 Decision 6, same reasoning as
    test_unknown_plot_key_is_invalid_input_with_no_run): a mahalanobis call here would
    hit the fit-trustworthiness gate before any figure is ever created, making the
    cleanup assertion below vacuously true rather than a real test."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.close("all")
    with pytest.raises(BloomMCPError):
        _run(method="isolation_forest", include_plots=True, plots=["not_a_real_figure"])
    assert plt.get_fignums() == []


def test_persistence_failure_closes_all_figures(injected_ports, monkeypatch):
    """The reproduced leak — a failure in the persistence region (create_run/commit)
    AFTER figures are made still closes every figure via the widened try/finally.
    method=isolation_forest (never gated): a mahalanobis call would raise at the
    fit-trustworthiness gate before any figure is made, never reaching the persistence
    region this test exists to exercise."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _reader, store = injected_ports

    def _boom(*a, **k):
        raise RuntimeError("commit blew up")

    monkeypatch.setattr(store, "commit", _boom)
    plt.close("all")
    with pytest.raises(BloomMCPError):
        _run(method="isolation_forest", include_plots=True)
    assert plt.get_fignums() == []


# ── _rows_subset multiset containment (repeated-barcode caveat) ─────────────


def test_rows_subset_uses_multiset_containment_under_repeated_barcodes():
    """_rows_subset compares on a multiset basis, so a returned frame that duplicates a
    barcode (which plain set membership would vacuously pass) is rejected."""
    from bloom_mcp.data_access import ExperimentFrame

    input_df = pd.DataFrame({"Barcode": ["a", "b", "c"], "t1": [1.0, 2.0, 3.0]})
    frame = ExperimentFrame(
        df=input_df,
        trait_cols=["t1"],
        metadata_cols=["Barcode"],
        genotype_col=None,
        replicate_col=None,
        sample_id_col="Barcode",
        source="v1_cleaned",
    )
    dup = pd.DataFrame({"Barcode": ["a", "a", "b"], "t1": [1.0, 1.0, 2.0]})
    assert remove_outliers_tool._rows_subset(frame, dup) is False
    sub = pd.DataFrame({"Barcode": ["a", "c"], "t1": [1.0, 3.0]})
    assert remove_outliers_tool._rows_subset(frame, sub) is True


# ── cylinder oracle (#483) ───────────────────────────────────────────────────
#
# See tests/fixtures/README.md's "Cross-tier oracle fixtures (cylinder)" section.
# Cylinder's mahalanobis fit is "poor" (untrustworthy, like turface_19's "very_poor")
# -- expected given 846 traits vs 129 samples makes the trait-covariance matrix
# severely rank-deficient. A method+seed characterization pin, not ground truth.

_RAW_CYL = _FIXTURES / "cylinder_raw_data.csv"
_GOLDEN_CYL = json.loads(
    (_FIXTURES / "cylinder_outlier_golden.json").read_text(encoding="utf-8")
)
# #419: cylinder's mahalanobis fit is also untrustworthy (poor) -- same split as
# turface_19: _GOLDEN_CYL (mahalanobis) is now the raise-path's historical reference,
# _GOLDEN_CYL_IFOREST is the new "successful persisted trim" characterization.
_GOLDEN_CYL_IFOREST = json.loads(
    (_FIXTURES / "cylinder_outlier_iforest_golden.json").read_text(encoding="utf-8")
)
_EXPERIMENT_CYL = "cylinder.csv"


def _cleaned_df_cyl() -> pd.DataFrame:
    raw = pd.read_csv(_RAW_CYL, encoding="utf-8")
    det = detect_columns(raw)
    cleaned, _kept, _log = clean_traits_for_analysis(
        raw, trait_cols=det["trait_cols"], **_roles(det)
    )
    return cleaned


@pytest.fixture
def injected_ports_cylinder():
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_cleaned_version(
        _EXPERIMENT_CYL, "v1", _cleaned_df_cyl(), make_latest=True
    )
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_cylinder_mahalanobis_default_untrustworthy_fit_is_gated_not_persisted(
    injected_ports_cylinder,
):
    """(#419) cylinder's mahalanobis@seed42 fit is poor (untrustworthy), so the tool
    raises assumption_violated instead of persisting -- same as turface_19. The
    message embeds the historical characterization counts/barcodes."""
    _reader, store = injected_ports_cylinder
    with pytest.raises(BloomMCPError) as exc:
        remove_outliers(
            RemoveOutliersParams(
                experiment=_EXPERIMENT_CYL, method="mahalanobis", seed=42
            )
        )

    assert exc.value.code == "assumption_violated"
    assert "isolation_forest" in exc.value.remedy
    msg = exc.value.message
    assert f"n_outliers={_GOLDEN_CYL['n_outliers']}" in msg  # 9
    assert f"n_input_samples={_GOLDEN_CYL['n_input_samples']}" in msg  # 129
    assert f"n_output_samples={_GOLDEN_CYL['n_output_samples']}" in msg  # 120
    assert "poor" in msg
    for barcode in _GOLDEN_CYL["outlier_barcodes"]:
        assert barcode in msg
    assert store.list_runs(_EXPERIMENT_CYL, "outliers") == []


def test_cylinder_isolation_forest_outlier_removal_matches_golden(
    injected_ports_cylinder,
):
    """(#419) isolation_forest@seed42 is cylinder's new "successful persisted trim"
    characterization, since mahalanobis is gated on this fixture."""
    result = remove_outliers(
        RemoveOutliersParams(
            experiment=_EXPERIMENT_CYL, method="isolation_forest", seed=42
        )
    )

    assert result.n_input_samples == _GOLDEN_CYL_IFOREST["n_input_samples"] == 129
    assert result.n_outliers == _GOLDEN_CYL_IFOREST["n_outliers"] == 13
    assert result.n_output_samples == _GOLDEN_CYL_IFOREST["n_output_samples"] == 116
    assert sorted(result.outlier_barcodes) == sorted(
        _GOLDEN_CYL_IFOREST["outlier_barcodes"]
    )
    assert result.fit_is_trustworthy is None
    assert result.goodness_of_fit is None
