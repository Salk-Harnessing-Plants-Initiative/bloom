"""Contract + oracle tests for the contract-wrapped ``plot_correlation_matrix`` tool (#466).

Converges the tool onto ``@as_mcp_tool`` — Pydantic I/O, structured ``BloomMCPError``, one
stamped ``Provenance``, versioned ``ResultStore`` persistence under its own tool class — mirroring
``qc_inspect``'s read-only, pre-clean EDA pattern (no ``require_clean``, reads the raw frame).
The delegate rendering (``create_correlation_heatmap``) is unchanged; the reported strong-
correlation counts are still computed directly in this module (never delegated), pinned against
an independent ``df.corr()`` computation.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import (
    FakeResultStore,
    SupabaseResultStore,
)
from bloom_mcp.tools import _ports
from bloom_mcp.sections.sleap_roots.analysis import (
    plot_correlation_matrix as plot_correlation_matrix_tool,
)
from bloom_mcp.sections.sleap_roots.analysis.plot_correlation_matrix import (
    PlotCorrelationMatrixParams,
    PlotCorrelationMatrixResult,
    plot_correlation_matrix,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_raw_data.csv"
_EXPERIMENT = "turface_19_raw.csv"


def _raw_df() -> pd.DataFrame:
    return pd.read_csv(_RAW, encoding="utf-8")


@pytest.fixture
def injected_ports():
    """FakeReader serving the raw fixture + FakeResultStore, via the _ports seam."""
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_experiment(_EXPERIMENT, _raw_df())
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _run(**overrides) -> PlotCorrelationMatrixResult:
    return plot_correlation_matrix(
        PlotCorrelationMatrixParams(experiment=_EXPERIMENT, **overrides)
    )


# ── numeric oracle: independent of the tool ─────────────────────────────────


def test_pins_one_off_diagonal_cell_and_high_correlation_counts(injected_ports):
    df = _raw_df()
    from bloom_mcp import experiment_utils as eu

    trait_cols = eu.detect_columns(df)["trait_cols"]
    expected_corr = df[trait_cols].corr()

    result = _run()

    assert result.n_traits == len(trait_cols)

    a, b = trait_cols[0], trait_cols[1]
    assert expected_corr.loc[a, b] == pytest.approx(
        df[[a, b]].corr().loc[a, b], abs=1e-12
    )

    upper = expected_corr.where(np.triu(np.ones(expected_corr.shape), k=1).astype(bool))
    expected_high_pos = int((upper > 0.7).sum().sum())
    expected_high_neg = int((upper < -0.7).sum().sum())
    assert result.strong_positive_correlations == expected_high_pos
    assert result.strong_negative_correlations == expected_high_neg


# ── tools/list presence ──────────────────────────────────────────────────────


def test_appears_in_tools_list_and_siblings_preserved():
    import asyncio

    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "sleap_roots_plot_correlation_matrix" in tools
    assert tools["sleap_roots_plot_correlation_matrix"].inputSchema is not None
    assert "sleap_roots_qc_inspect" in tools  # additive — sibling not removed


# ── schema round-trip ────────────────────────────────────────────────────────


def test_valid_input_output_round_trip(injected_ports):
    result = _run()
    again = PlotCorrelationMatrixResult.model_validate(
        json.loads(result.model_dump_json())
    )
    assert again.strong_positive_correlations == result.strong_positive_correlations


def test_missing_experiment_is_invalid_input():
    with pytest.raises(BloomMCPError) as exc:
        plot_correlation_matrix({})
    assert exc.value.code == "invalid_input"


def test_unknown_field_is_rejected():
    """extra="forbid" (#466 review round 5): an unknown field isn't currently
    exploitable — it would be dropped before persistence either way — but silently
    accepting it masks a caller typo. Passed as a raw dict (not a pre-constructed Params
    instance) so @as_mcp_tool's own input-validation path — the one a real MCP caller
    goes through — is what's under test."""
    with pytest.raises(BloomMCPError) as exc:
        plot_correlation_matrix({"experiment": _EXPERIMENT, "trait_column": ["t1"]})
    assert exc.value.code == "invalid_input"


def test_empty_trait_columns_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=[])
    assert exc.value.code == "invalid_input"


def test_unknown_trait_column_is_invalid_input_naming_it(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["NoSuchTrait"])
    assert exc.value.code == "invalid_input"
    assert "NoSuchTrait" in exc.value.message


def test_non_numeric_trait_column_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["geno"])
    assert exc.value.code == "invalid_input"


def test_duplicate_trait_columns_is_invalid_input(injected_ports):
    """A duplicate is NOT harmless here (unlike qc_clean/qc_inspect's non-certified
    validation): it would silently count a self-correlation (r=1.0) as a "strong positive
    correlation" in a permanent, provenance-stamped ResultStore artifact (#466 review).
    """
    df = _raw_df()
    from bloom_mcp import experiment_utils as eu

    trait = eu.detect_columns(df)["trait_cols"][0]
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=[trait, trait])
    assert exc.value.code == "invalid_input"
    assert trait in exc.value.message


def test_metadata_only_frame_with_no_traits_is_invalid_input():
    df = pd.DataFrame(
        {"Barcode": ["b0", "b1"], "geno": ["g1", "g2"], "note": ["x", "y"]}
    )
    reader = FakeReader()
    reader.add_experiment("meta_only.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        with pytest.raises(BloomMCPError) as exc:
            plot_correlation_matrix(
                PlotCorrelationMatrixParams(experiment="meta_only.csv")
            )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert exc.value.code == "invalid_input"


@pytest.mark.parametrize(
    "bad",
    [
        "../../app/.env",
        "/etc/passwd",
        "sub/dir/x.csv",
        "..\\..\\app\\.env",
        "sub\\dir\\x.csv",
        "..",
        ".",
        "",
    ],
)
def test_experiment_path_traversal_is_rejected_before_any_read(
    injected_ports, monkeypatch, bad
):
    calls = {"n": 0}
    real = plot_correlation_matrix_tool.create_correlation_heatmap

    def _spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _spy
    )
    with pytest.raises(BloomMCPError) as exc:
        plot_correlation_matrix(PlotCorrelationMatrixParams(experiment=bad))
    assert exc.value.code == "invalid_input"
    assert calls["n"] == 0


# ── provenance + links (not blobs) ───────────────────────────────────────────


def test_provenance_stamped_seed_none_and_links_returned(injected_ports):
    _reader, store = injected_ports
    result = _run()

    stored = store.get_run(_EXPERIMENT, "correlation_matrix", "latest")
    assert stored.tool == "plot_correlation_matrix"
    assert stored.seed is None  # deterministic, no random_state

    assert result.run_ref == stored.run_ref
    assert result.manifest_path == stored.manifest_path
    assert set(result.outputs) == set(stored.output_keys)

    assert set(result.output_links) == set(result.outputs)
    for name, key in result.outputs.items():
        link = result.output_links[name]
        assert link.key == key
        assert link.url
        assert link.sha256 == stored.output_sha256[name]
        assert link.size_bytes >= 0

    # Links, not blobs: no inline field carries a large payload (no base64 figure).
    dumped = result.model_dump()
    assert not any(
        isinstance(v, (list, dict)) and len(str(v)) > 5000 for v in dumped.values()
    )


def test_source_content_addressed_in_manifest(injected_ports):
    """Actually asserts source/input content-addressing (#466 review round 3: the previous
    version of this test only checked output hashing, not the based_on_version/
    resolved_trait_columns fields its name promised)."""
    _reader, store = injected_ports
    result = _run()
    stored = store.get_run(_EXPERIMENT, "correlation_matrix", "latest")
    assert stored.input_validation is None  # no input_validation for this tool
    assert stored.output_sha256  # every committed output is hashed
    assert stored.based_on_version == result.source == "raw"
    assert stored.params["resolved_trait_columns"] == result.resolved_trait_columns


def test_resolved_trait_columns_recorded_when_trait_columns_omitted(injected_ports):
    """When trait_columns is omitted, auto-detection resolves the actual trait list — that
    exact list must be recoverable from the manifest later, not just its count (#466 review:
    previously only n_traits was recorded, and auto-detection is data-dependent so it can't
    be safely re-derived from a manifest read months later)."""
    _reader, store = injected_ports
    result = _run()
    from bloom_mcp import experiment_utils as eu

    expected = eu.detect_columns(_raw_df())["trait_cols"]
    assert result.resolved_trait_columns == expected
    stored = store.get_run(_EXPERIMENT, "correlation_matrix", "latest")
    assert stored.params["resolved_trait_columns"] == expected


def test_zero_variance_trait_excluded_from_counts_and_reported(injected_ports):
    """A constant (zero-variance) trait's Pearson correlation against every other trait is
    NaN, which counts toward neither strong_positive_correlations nor
    strong_negative_correlations (NaN > 0.7 is False) — the result must name it explicitly
    rather than silently under-reporting (#466 review)."""
    df = _raw_df()
    df["constant_trait"] = 1.0
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert "constant_trait" in result.zero_variance_traits
    assert result.heatmap_caveat is not None
    assert "1" in result.heatmap_caveat  # names the flagged count


def test_all_selected_traits_zero_variance_is_assumption_violated(monkeypatch):
    """The >=2-column guard only counts columns, not variance — 2+ constant/all-NaN traits
    must still be rejected (as assumption_violated, discovered only after reading the data,
    unlike the pure input-shape invalid_input single-trait guard) rather than committing a
    meaningless all-NaN heatmap as a permanent artifact (#466 review)."""
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(8)],
            "geno": ["g1", "g2"] * 4,
            "const_a": [1.0] * 8,
            "const_b": [2.0] * 8,
        }
    )
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, df)
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    calls = {"n": 0}
    real = plot_correlation_matrix_tool.create_correlation_heatmap

    def _spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _spy
    )
    try:
        with pytest.raises(BloomMCPError) as exc:
            plot_correlation_matrix(PlotCorrelationMatrixParams(experiment=_EXPERIMENT))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert exc.value.code == "assumption_violated"
    assert calls["n"] == 0
    assert store.list_runs(_EXPERIMENT, "correlation_matrix") == []


def test_heatmap_caveat_is_none_when_nothing_is_flagged(injected_ports):
    result = _run()
    assert result.zero_variance_traits == []
    assert result.low_overlap_trait_pairs == []
    assert result.heatmap_caveat is None


def test_heatmap_still_renders_from_the_full_unmasked_frame(
    injected_ports, monkeypatch
):
    """The disclosure (heatmap_caveat) is honest only if the delegate genuinely receives no
    masking — pins that create_correlation_heatmap is called with the same frame/trait_cols
    regardless of what zero_variance_traits/low_overlap_trait_pairs flag (#466 review: the
    image is NOT masked the way the summary is)."""
    df = _raw_df()
    df["constant_trait"] = 1.0
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, df)
    _ports.configure(reader=reader, store=FakeResultStore())
    captured = {}
    real = plot_correlation_matrix_tool.create_correlation_heatmap

    def _spy(passed_df, passed_trait_cols, *a, **k):
        captured["trait_cols"] = list(passed_trait_cols)
        return real(passed_df, passed_trait_cols, *a, **k)

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _spy
    )
    try:
        result = plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert "constant_trait" in result.zero_variance_traits
    # The flagged trait was still passed to the delegate, unmasked/unexcluded.
    assert "constant_trait" in captured["trait_cols"]
    assert captured["trait_cols"] == result.resolved_trait_columns


def test_heatmap_caveat_annotated_directly_onto_the_saved_figure(
    injected_ports, monkeypatch
):
    """#466 review round 4 blocking finding: the first version of this fix was JSON-only —
    a caller who downloads the PNG (or opens it via output_links) without also reading the
    JSON response saw zero indication a cell might be spurious. The warning must be drawn
    onto the actual Figure before savefig, not just returned in the result."""
    df = _raw_df()
    df["constant_trait"] = 1.0
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, df)
    _ports.configure(reader=reader, store=FakeResultStore())
    captured = {}
    real = plot_correlation_matrix_tool.create_correlation_heatmap

    def _spy(*a, **k):
        fig = real(*a, **k)
        captured["fig"] = fig
        return fig

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _spy
    )
    try:
        result = plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert result.heatmap_caveat is not None
    fig = captured["fig"]
    # plt.close(fig) detaches it from pyplot's registry but the Python object (and its
    # child artists) is still fully inspectable via this held reference.
    assert len(fig.texts) >= 1
    assert any(result.heatmap_caveat in t.get_text() for t in fig.texts)


def test_no_annotation_added_when_nothing_is_flagged(injected_ports, monkeypatch):
    captured = {}
    real = plot_correlation_matrix_tool.create_correlation_heatmap

    def _spy(*a, **k):
        fig = real(*a, **k)
        captured["fig"] = fig
        return fig

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _spy
    )
    result = _run()
    assert result.heatmap_caveat is None
    assert captured["fig"].texts == []


def test_heatmap_caveat_stamped_into_manifest_params(injected_ports):
    """#466 review round 4 blocking finding: resolved_trait_columns was stamped into the
    persisted run's params, but heatmap_caveat was only ever added to the live response —
    a later reader of the manifest (list_existing_analyses/manifest_path, the workflow this
    field exists for) got nothing. Must mirror the resolved_trait_columns pattern."""
    _reader, store = injected_ports
    df = _raw_df()
    df["constant_trait"] = 1.0
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, df)
    _ports.configure(reader=reader, store=store)
    try:
        result = plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert result.heatmap_caveat is not None
    stored = store.get_run(_EXPERIMENT, "correlation_matrix", "latest")
    assert stored.params["heatmap_caveat"] == result.heatmap_caveat


def test_heatmap_caveat_stamped_as_none_when_nothing_flagged(injected_ports):
    _reader, store = injected_ports
    _run()
    stored = store.get_run(_EXPERIMENT, "correlation_matrix", "latest")
    assert stored.params["heatmap_caveat"] is None


def test_single_trait_selection_is_invalid_input(injected_ports, monkeypatch):
    """A correlation view needs a pair — a lone trait must be rejected, not silently
    committed as a meaningless 1x1 heatmap (#466 review)."""
    df = _raw_df()
    from bloom_mcp import experiment_utils as eu

    trait = eu.detect_columns(df)["trait_cols"][0]
    calls = {"n": 0}
    real = plot_correlation_matrix_tool.create_correlation_heatmap

    def _spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _spy
    )
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=[trait])
    assert exc.value.code == "invalid_input"
    assert calls["n"] == 0


def test_low_overlap_pair_excluded_from_counts_and_reported(injected_ports):
    """Raw, uncleaned data can have disjoint per-trait missingness: two traits overlapping
    in only 2 non-null rows are *always* perfectly (anti)correlated, a spurious "strong
    correlation" from a near-empty sample — min_periods must exclude it from the counts,
    and the pair must be named rather than silently miscounted (#466 review)."""
    n = 20
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(n)],
            "geno": ["g1", "g2"] * (n // 2),
            # Disjoint missingness: only rows 8-9 have both non-null, and those 2 points
            # are perfectly correlated (a deterministic property of any 2-point line).
            "sparse_a": [float(i) if i < 10 else None for i in range(n)],
            "sparse_b": [float(i) if i >= 8 else None for i in range(n)],
        }
    )
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    overlap = df[["sparse_a", "sparse_b"]].dropna()
    assert (
        len(overlap) == 2
    )  # confirms the fixture actually exercises the near-empty case
    assert ["sparse_a", "sparse_b"] in result.low_overlap_trait_pairs
    assert result.strong_positive_correlations == 0
    assert result.zero_variance_traits == []
    assert result.heatmap_caveat is not None


@pytest.mark.parametrize(
    "n_overlap, expect_flagged",
    [(10, False), (9, True)],  # boundary: min_periods=10, "< 10" is flagged
)
def test_low_overlap_boundary_at_min_periods(n_overlap, expect_flagged):
    """#466 review round 4: the only prior overlap test used n=2, deep inside the flagged
    region — nothing pinned the actual _MIN_CORR_OVERLAP=10 boundary itself, so an off-by-one
    (<= instead of <, or the wrong constant) would sail through the full suite undetected.
    """
    assert plot_correlation_matrix_tool._MIN_CORR_OVERLAP == 10
    n = 20
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(n)],
            "geno": ["g1", "g2"] * (n // 2),
            "sparse_a": [float(i) if i < n_overlap else None for i in range(n)],
            "sparse_b": [float(i) if i < n_overlap else None for i in range(n)],
        }
    )
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    overlap = df[["sparse_a", "sparse_b"]].dropna()
    assert len(overlap) == n_overlap  # confirms the fixture hits the exact boundary
    is_flagged = ["sparse_a", "sparse_b"] in result.low_overlap_trait_pairs
    assert is_flagged is expect_flagged


def test_reads_raw_even_when_a_cleaned_version_already_exists():
    reader = FakeReader()
    raw = _raw_df()
    reader.add_experiment(_EXPERIMENT, raw)
    cleaned = raw.copy()
    cleaned["Root_Biomass_mg"] = 0.0  # distinguishable from raw
    reader.add_cleaned_version(_EXPERIMENT, "v1", cleaned)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert result.source == "raw"


# ── delegation pinning (spy) ─────────────────────────────────────────────────


def test_delegates_rendering_and_never_calls_vendored_cleanup(
    injected_ports, monkeypatch
):
    calls = {"n": 0}
    real = plot_correlation_matrix_tool.create_correlation_heatmap

    def _spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _spy
    )
    _run()
    assert calls["n"] == 1


# ── no figure-handle leak + headless backend ────────────────────────────────


def test_no_figure_handle_leak_and_agg_backend(injected_ports):
    import matplotlib
    import matplotlib.pyplot as plt

    assert matplotlib.get_backend().lower() == "agg"
    before = len(plt.get_fignums())
    _run()
    assert len(plt.get_fignums()) == before


# ── error envelope ───────────────────────────────────────────────────────────


def test_unresolvable_experiment_errors_with_no_run(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError):
        plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment="does_not_exist.csv")
        )
    assert store.list_runs("does_not_exist.csv", "correlation_matrix") == []


def test_delegate_raise_is_structured_without_leaking(injected_ports, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _boom
    )
    with pytest.raises(BloomMCPError) as exc:
        _run()
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "secret" not in msg and "/var" not in msg and "db.internal" not in msg


def test_render_failure_cleans_staging_and_commits_nothing(injected_ports, monkeypatch):
    _reader, store = injected_ports

    captured = {}
    real_create = store.create_run

    def _spy_create(*a, **k):
        run = real_create(*a, **k)
        captured["staging_dir"] = run.staging_dir
        return run

    monkeypatch.setattr(store, "create_run", _spy_create)

    def _boom(*a, **k):
        raise RuntimeError("render failed after the run dir was created")

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _boom
    )

    with pytest.raises(BloomMCPError):
        _run()
    assert store.list_runs(_EXPERIMENT, "correlation_matrix") == []
    assert not captured["staging_dir"].exists()


def test_commit_failure_cleans_staging_and_commits_nothing(injected_ports, monkeypatch):
    _reader, store = injected_ports
    captured = {}
    real_create = store.create_run

    def _spy_create(*a, **k):
        run = real_create(*a, **k)
        captured["staging_dir"] = run.staging_dir
        return run

    monkeypatch.setattr(store, "create_run", _spy_create)
    store.fail_next_commit(_EXPERIMENT, "correlation_matrix")

    with pytest.raises(BloomMCPError):
        _run()
    assert store.list_runs(_EXPERIMENT, "correlation_matrix") == []
    assert not captured["staging_dir"].exists()


# ── ResultStore write-path failures surface as tool_error, not a bare internal_error ref
# (#640/#466 review: errors=(ExperimentReadError,) alone swallowed a CommitFailedError/
# ManifestReadError from store.create_run()/commit() into a generic internal_error ref) ──


def test_commit_failure_surfaces_as_tool_error(injected_ports):
    _reader, store = injected_ports
    store.fail_next_commit(_EXPERIMENT, "correlation_matrix")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "commit failed for correlation_matrix" in exc.value.message


def test_manifest_read_failure_surfaces_as_tool_error(injected_ports):
    _reader, store = injected_ports
    store.fail_next_read(_EXPERIMENT, "correlation_matrix")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "manifest read failure" in exc.value.message
