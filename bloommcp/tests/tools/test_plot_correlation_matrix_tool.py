"""Contract + oracle tests for the contract-wrapped ``plot_correlation_matrix`` tool (#466).

Converges the tool onto ``@as_mcp_tool`` — Pydantic I/O, structured ``BloomMCPError``, one
stamped ``Provenance``, versioned ``ResultStore`` persistence under its own tool class — mirroring
``qc_inspect``'s read-only, pre-clean EDA pattern (no ``require_clean``, reads the raw frame).
The delegate rendering (``create_correlation_heatmap``) is unchanged; the reported strong-
correlation counts are still computed directly in this module (never delegated), pinned against
an independent ``df.corr()`` computation.

The final section covers the per-pair disclosure added by #784/#785: ``strong_correlation_pairs``
with its overlap/Fisher-interval evidence and the uncapped overlap summaries beside it, and
``locally_constant_trait_pairs`` — the third blank-cell bucket, whose defining property (every
off-diagonal ``NaN`` claimed by exactly one of the three lists) is pinned directly rather than
only sampled.
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

    assert result.n_traits_plotted == len(trait_cols)

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
    previously only n_traits_plotted was recorded, and auto-detection is data-dependent so it can't
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
    # Names the actual flagged trait, not just a count (#466 review round 6) — a PNG-only
    # viewer can cross-reference this name against the image's own axis labels.
    assert "constant_trait" in result.heatmap_caveat


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


def test_single_non_null_value_trait_is_reported_as_zero_variance(injected_ports):
    """A trait with exactly ONE non-null value has std NaN, not 0 (ddof=1 needs two
    observations), so `not (std > 0)` sweeps it into zero_variance_traits alongside the
    constant and all-NaN cases. That grouping is correct — all three are unconditionally
    uncorrelatable — but the field described only two of the three until #466 round 7.
    Pinned so the documented taxonomy and the actual behaviour stay in step."""
    n = 20
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(n)],
            "geno": ["g1", "g2"] * (n // 2),
            "t1": [float(i) for i in range(n)],
            "t2": [float(2 * i + 1) for i in range(n)],
            "one_value": [3.0] + [None] * (n - 1),
        }
    )
    assert df["one_value"].notna().sum() == 1
    assert pd.isna(df["one_value"].std(skipna=True))  # NaN, not 0 — the subtle case
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert "one_value" in result.zero_variance_traits
    # and it is not double-reported under the other reason
    assert all("one_value" not in pair for pair in result.low_overlap_trait_pairs)


def test_heatmap_caveat_is_none_when_nothing_is_flagged(injected_ports):
    result = _run()
    assert result.zero_variance_traits == []
    assert result.low_overlap_trait_pairs == []
    assert result.heatmap_caveat is None


def test_heatmap_caveat_caps_names_at_ten_with_a_remainder_count():
    """#466 review round 6: heatmap_caveat now names actual flagged traits, not just a
    count — capped so a wide (cylinder-scale) selection with many flagged traits doesn't
    produce an unreadably long footnote. 15 constant traits: the first 10 are named, the
    remaining 5 collapse to a "+5 more" — the full, uncapped list is always in
    zero_variance_traits."""
    n = 12
    data = {
        "Barcode": [f"b{i}" for i in range(n)],
        "geno": ["g1", "g2"] * (n // 2),
        # 2 non-constant traits so the >=2-non-constant guard doesn't fire before
        # the 15 constant traits below ever reach zero_variance_traits/heatmap_caveat.
        "t1": [float(i) for i in range(n)],
        "t2": [float(2 * i + 1) for i in range(n)],
    }
    for i in range(15):
        data[f"const_{i:02d}"] = [float(i)] * n
    df = pd.DataFrame(data)
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert len(result.zero_variance_traits) == 15
    assert result.heatmap_caveat.count("const_") == 10
    assert "+5 more" in result.heatmap_caveat


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


def test_full_uncapped_disclosure_lists_are_recoverable_from_the_manifest(
    injected_ports,
):
    """#466 review round 7: the stamped heatmap_caveat is capped at 10 names and then
    tells the reader to "see zero_variance_traits/low_overlap_trait_pairs for the
    complete list" — but those lists lived only in the live response, so a manifest-only
    reader could not follow that instruction past the cap. This is the case the module's
    "a later manifest read gets the same signal a live call did" claim was false for:
    15 flagged traits, only 10 nameable in the caveat, all 15 recoverable from the run.
    """
    _reader, store = injected_ports
    n = 12
    data = {
        "Barcode": [f"b{i}" for i in range(n)],
        "geno": ["g1", "g2"] * (n // 2),
        "t1": [float(i) for i in range(n)],
        "t2": [float(2 * i + 1) for i in range(n)],
    }
    for i in range(15):
        data[f"const_{i:02d}"] = [float(i)] * n
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, pd.DataFrame(data))
    _ports.configure(reader=reader, store=store)
    try:
        result = plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    # the caveat itself can only name 10 of the 15 — that is the gap being closed
    assert result.heatmap_caveat.count("const_") == 10
    assert "+5 more" in result.heatmap_caveat

    stored = store.get_run(_EXPERIMENT, "correlation_matrix", "latest")
    assert stored.params["zero_variance_traits"] == result.zero_variance_traits
    assert len(stored.params["zero_variance_traits"]) == 15
    assert stored.params["low_overlap_trait_pairs"] == result.low_overlap_trait_pairs


def test_low_overlap_pairs_survive_a_manifest_json_round_trip(injected_ports):
    """The pairs are list[list[str]], not tuples — a tuple would come back from JSON as a
    list and silently break equality for any manifest reader comparing against the live
    response. Pins the serialized shape, not just the in-memory one."""
    _reader, store = injected_ports
    n = 20
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(n)],
            "geno": ["g1", "g2"] * (n // 2),
            # Disjoint missingness — same shape as the low-overlap oracle above: only
            # rows 8-9 overlap, and neither column is constant, so the pair lands in
            # low_overlap_trait_pairs rather than being absorbed by zero_variance_traits.
            "sparse_a": [float(i) if i < 10 else None for i in range(n)],
            "sparse_b": [float(i) if i >= 8 else None for i in range(n)],
        }
    )
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, df)
    _ports.configure(reader=reader, store=store)
    try:
        result = plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert result.low_overlap_trait_pairs
    stored = store.get_run(_EXPERIMENT, "correlation_matrix", "latest")
    round_tripped = json.loads(json.dumps(stored.params["low_overlap_trait_pairs"]))
    assert round_tripped == stored.params["low_overlap_trait_pairs"]
    assert all(isinstance(pair, list) for pair in round_tripped)


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
    # Names the actual flagged pair, not just a count (#466 review round 6).
    assert "sparse_a" in result.heatmap_caveat
    assert "sparse_b" in result.heatmap_caveat


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


def test_delegates_rendering_exactly_once(injected_ports, monkeypatch):
    """Renamed in #466 review round 6: this test's body only ever checked the delegate
    call count — it never asserted anything about vendored cleanup, so the original name
    (`..._and_never_calls_vendored_cleanup`) promised more than it verified. The "never
    calls the vendored bloom_mcp.data_cleanup" guarantee is structural (this module has no
    import of it at all — see the module docstring), not something a runtime spy on an
    unrelated module would meaningfully test here."""
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


# ── FIGURE_REGISTRY_LOCK participation (#466 review round 6 — the pre-#466 versions of
# these 3 tool files acquire this same process-wide lock around their figure-creating
# delegate call, per sibling PR #726/#721; this tool's rewritten body must too, or these
# 3 newly-converged tools become the only matplotlib call sites left unprotected against
# the concurrent-figure-creation race the lock exists to close. Round 7: creation-only
# locking was a HALF-fix — `plt.close` -> `Gcf.destroy_fig` scans the same shared
# `Gcf.figs` dict a concurrent create mutates, so the close path is locked and pinned
# too) ────────────────────────────────────────────────────────────────────────────────


def test_shares_the_same_figure_registry_lock_object():
    from bloom_mcp.tools import _plots

    assert (
        plot_correlation_matrix_tool.FIGURE_REGISTRY_LOCK is _plots.FIGURE_REGISTRY_LOCK
    )


def test_delegate_runs_while_holding_the_figure_registry_lock(
    injected_ports, monkeypatch
):
    """The creating delegate call executes with the REAL process-wide lock held.

    Replaces a count-based spy on this module's `FIGURE_REGISTRY_LOCK` attribute: since
    #726 landed, creation goes through `call_with_figure_cleanup`, which acquires
    `_plots.FIGURE_REGISTRY_LOCK` directly, so a module-attribute spy could no longer
    see it. Asserting `.locked()` at the moment the delegate runs is the property that
    actually matters, and it survives either implementation (#466 review round 7).
    """
    from bloom_mcp.tools import _plots

    real_delegate = plot_correlation_matrix_tool.create_correlation_heatmap
    held: list[bool] = []

    def _spy(*a, **k):
        held.append(_plots.FIGURE_REGISTRY_LOCK.locked())
        return real_delegate(*a, **k)

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _spy
    )
    _run()
    assert held == [True]


def test_routes_figure_creation_through_call_with_figure_cleanup(
    injected_ports, monkeypatch
):
    """Creation goes through the shared `call_with_figure_cleanup` (#721/#726), not an
    ad hoc `with FIGURE_REGISTRY_LOCK:` — `_plots.py`'s lock comment promises every
    figure-creating call site in bloommcp does, and a refactor back to a bare `with`
    would silently drop the mid-render leak cleanup that closes #725 for this tool."""
    from bloom_mcp.tools import _plots

    real_helper = _plots.call_with_figure_cleanup
    calls = {"n": 0}

    def _spy(fn):
        calls["n"] += 1
        return real_helper(fn)

    monkeypatch.setattr(plot_correlation_matrix_tool, "call_with_figure_cleanup", _spy)
    _run()
    assert calls["n"] == 1


def test_delegate_raise_after_allocating_leaks_no_figure(injected_ports, monkeypatch):
    """A delegate that allocates a figure and then raises mid-render must not leak it:
    `figures`/`fig` is never assigned, so this tool's own `finally` cannot reach it — only
    `call_with_figure_cleanup`'s fignum-diff cleanup can (#721/#726, pinned here for #466).
    """
    import matplotlib.pyplot as plt

    def _allocate_then_boom(*a, **k):
        plt.figure()
        raise RuntimeError("renderer died after allocating")

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _allocate_then_boom
    )
    before = plt.get_fignums()
    with pytest.raises(BloomMCPError):
        _run()
    assert plt.get_fignums() == before


def test_closes_figures_while_holding_the_figure_registry_lock(
    injected_ports, monkeypatch
):
    """The close runs under the lock — the round-6 gap this pins shut.

    Asserts the property directly (was the lock actually held at the moment
    ``plt.close`` ran?) rather than counting acquisitions, so a later refactor
    that still enters the lock twice but moves the close back outside it fails
    here rather than silently reopening the race (#466 review round 7).
    """
    real_lock = plot_correlation_matrix_tool.FIGURE_REGISTRY_LOCK
    real_close = plot_correlation_matrix_tool.plt.close
    held: list[bool] = []

    def _spy_close(fig):
        held.append(real_lock.locked())
        return real_close(fig)

    monkeypatch.setattr(plot_correlation_matrix_tool.plt, "close", _spy_close)
    _run()
    assert held, "the tool never closed a figure"
    assert all(held), "plt.close ran without FIGURE_REGISTRY_LOCK held"


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


# ── per-pair disclosure: strong-pair evidence + the third blank-cell bucket ──────────
# (#784/#785, openspec change add-bloommcp-corr-pair-disclosure). The tests below build
# their own degenerate frames rather than decorating _raw_df(), so each fixture's
# degeneracy is visible at the point of use — same approach as the low-overlap oracles
# above, factored through one helper because this section adds many of them.


def _run_with_frame(df: pd.DataFrame, store=None) -> PlotCorrelationMatrixResult:
    """Run the tool over a purpose-built frame, restoring the real ports afterwards."""
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, df)
    _ports.configure(reader=reader, store=store or FakeResultStore())
    try:
        return plot_correlation_matrix(
            PlotCorrelationMatrixParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


_META_COLS = ("Barcode", "geno")


def _meta(n: int) -> dict:
    """The metadata columns every fixture frame in this file carries."""
    return {"Barcode": [f"b{i}" for i in range(n)], "geno": ["g1", "g2"] * (n // 2)}


def _strong_pairs_oracle(df: pd.DataFrame) -> dict:
    """Independent recomputation: {(a, b): (r, overlap_n)} for every |r| > 0.7 pair."""
    traits = [c for c in df.columns if c not in ("Barcode", "geno")]
    out = {}
    for i, a in enumerate(traits):
        for b in traits[i + 1 :]:
            overlap = df[[a, b]].dropna()
            if len(overlap) < plot_correlation_matrix_tool._MIN_CORR_OVERLAP:
                continue
            r = overlap[a].corr(overlap[b])
            if pd.notna(r) and abs(r) > 0.7:
                out[(a, b)] = (float(r), len(overlap))
    return out


def _reject_constant(token):  # pragma: no cover - only called on invalid JSON
    raise AssertionError(f"non-finite JSON token {token!r} reached the payload")


def _collinear_frame(n_traits: int, n_rows: int, seed: int = 0) -> pd.DataFrame:
    """A heavily collinear frame (one latent factor) — the shape real root-trait data has,
    where nearly every pair clears |r| > 0.7."""
    rng = np.random.default_rng(seed)
    latent = rng.normal(size=n_rows)
    data = dict(_meta(n_rows))
    for t in range(n_traits):
        values = latent * (t + 1) + 0.05 * rng.normal(size=n_rows)
        # Stagger missingness so the pairs land at a spread of distinct overlaps.
        n_missing = t % 7
        if n_missing:
            values[-n_missing:] = np.nan
        data[f"t{t}"] = values
    return pd.DataFrame(data)


def test_strong_pairs_report_overlap_and_ci(injected_ports):
    """#784: strong_positive/negative_correlations are bare counts — a caller cannot tell
    whether they rest on n=10 (r=0.7 has a 95% CI of ~[0.13, 0.92] there) or n=1000. Each
    counted pair must carry the overlap and interval behind it."""
    n = 30
    rng = np.random.default_rng(4)
    base = rng.normal(size=n)
    other = rng.normal(size=n)
    df = pd.DataFrame(
        {
            **_meta(n),
            # Strong over all 30 rows.
            "full_a": base,
            "full_b": base * 2 + 0.1 * rng.normal(size=n),
            # Strong over exactly 10 rows — clears min_periods, but only just. Driven by
            # its own latent series, so no pair here is exactly collinear (r == 1.0 would
            # legitimately report a null interval, which this test is not about).
            "thin_a": [float(v) if i < 10 else None for i, v in enumerate(other)],
            "thin_b": [
                float(v * 3 + 0.1 * w) if i < 10 else None
                for i, (v, w) in enumerate(zip(other, rng.normal(size=n)))
            ],
        }
    )
    result = _run_with_frame(df)
    oracle = _strong_pairs_oracle(df)

    assert result.strong_correlation_pairs, "no strong pairs reported"
    reported = {
        tuple(p.traits): (p.r, p.overlap_n) for p in result.strong_correlation_pairs
    }
    assert set(reported) == set(oracle)
    for pair, (r, n_overlap) in oracle.items():
        assert reported[pair][1] == n_overlap
        assert reported[pair][0] == pytest.approx(r)
    assert ("thin_a", "thin_b") in reported
    assert reported[("thin_a", "thin_b")][1] == 10
    for p in result.strong_correlation_pairs:
        assert p.ci_low is not None and p.ci_high is not None
        assert p.ci_low <= p.r <= p.ci_high


def test_strong_pairs_ordered_by_ascending_overlap(injected_ports):
    """Ascending overlap is what makes the cap safe (design.md Decision 2): it truncates the
    best-supported end, so the least-supported pair is always reported."""
    result = _run_with_frame(_collinear_frame(n_traits=6, n_rows=40))
    overlaps = [p.overlap_n for p in result.strong_correlation_pairs]
    assert len(set(overlaps)) > 1, "fixture must span distinct overlaps"
    assert overlaps == sorted(overlaps)


def test_strong_pairs_capped_but_counts_stay_true(injected_ports):
    """At cylinder scale an uncapped list can exceed 100,000 entries (~12 MB of JSON). The
    cap must not touch the authoritative counts, and must not hide the weakest pair."""
    cap = plot_correlation_matrix_tool._MAX_STRONG_PAIRS_REPORTED
    df = _collinear_frame(n_traits=16, n_rows=60)  # C(16,2) = 120 pairs
    result = _run_with_frame(df)
    oracle = _strong_pairs_oracle(df)

    assert len(oracle) > cap, "fixture must exceed the cap"
    assert len(result.strong_correlation_pairs) == cap
    assert (
        result.strong_positive_correlations + result.strong_negative_correlations
    ) == len(oracle)
    # The smallest-overlap strong pair survives truncation.
    assert result.strong_correlation_pairs[0].overlap_n == min(
        n for _r, n in oracle.values()
    )


def test_strong_pair_overlap_summaries_are_uncapped(injected_ports):
    """A capped list is a biased sample — 50 low-n pairs out of 120 read as if the whole
    population were poorly supported. The min/median/max scalars are computed over *all*
    strong pairs so a caller can tell representative from exceptional (#784)."""
    df = _collinear_frame(n_traits=16, n_rows=60)
    result = _run_with_frame(df)
    all_overlaps = sorted(n for _r, n in _strong_pairs_oracle(df).values())

    assert len(result.strong_correlation_pairs) < len(all_overlaps)
    assert result.strong_pair_overlap_min == all_overlaps[0]
    assert result.strong_pair_overlap_max == all_overlaps[-1]
    assert result.strong_pair_overlap_median == pytest.approx(
        float(np.median(all_overlaps))
    )
    assert (
        result.strong_pair_overlap_min == result.strong_correlation_pairs[0].overlap_n
    )


def test_fisher_ci_matches_closed_form(injected_ports):
    """Oracle for the interval itself: tanh(arctanh(r) +/- z / sqrt(n - 3)), with z the
    exact two-sided 95% normal quantile — written as a literal here rather than imported from
    the module, so this stays an independent check of the formula."""
    result = _run_with_frame(_collinear_frame(n_traits=5, n_rows=40))
    assert result.strong_correlation_pairs
    for p in result.strong_correlation_pairs:
        z = np.arctanh(p.r)
        se = 1.959963984540054 / np.sqrt(p.overlap_n - 3)
        assert p.ci_low == pytest.approx(float(np.tanh(z - se)), abs=1e-9)
        assert p.ci_high == pytest.approx(float(np.tanh(z + se)), abs=1e-9)


def test_perfectly_collinear_pair_reports_null_ci(injected_ports):
    """r == +/-1 puts arctanh at infinity and collapses the interval to [r, r]. Reporting
    that zero-width interval would claim perfect precision — exactly the false confidence
    this field exists to puncture — so it is reported as null (design.md Decision 3)."""
    n = 20
    df = pd.DataFrame(
        {
            **_meta(n),
            "exact_a": [float(i) for i in range(n)],
            "exact_b": [float(i) * 3 + 1 for i in range(n)],  # exactly collinear
        }
    )
    result = _run_with_frame(df)
    pairs = {tuple(p.traits): p for p in result.strong_correlation_pairs}
    collinear = pairs[("exact_a", "exact_b")]

    assert collinear.r == pytest.approx(1.0)
    assert collinear.ci_low is None
    assert collinear.ci_high is None


def test_ci_is_null_below_four_overlap(injected_ports, monkeypatch):
    """The Fisher standard error needs n > 3. Unreachable while the floor is 10, but the
    constant is this module's own to change now (design.md Decision 5), so the branch is
    exercised rather than left to a distant invariant."""
    monkeypatch.setattr(plot_correlation_matrix_tool, "_MIN_CORR_OVERLAP", 2)
    n = 12
    df = pd.DataFrame(
        {
            **_meta(n),
            "tiny_a": [1.0, 2.0, 3.5] + [None] * (n - 3),
            "tiny_b": [2.0, 4.1, 7.0] + [None] * (n - 3),
            "filler": [float(i % 5) for i in range(n)],
        }
    )
    result = _run_with_frame(df)
    pairs = {tuple(p.traits): p for p in result.strong_correlation_pairs}

    assert ("tiny_a", "tiny_b") in pairs
    tiny = pairs[("tiny_a", "tiny_b")]
    assert tiny.overlap_n == 3
    assert tiny.ci_low is None and tiny.ci_high is None


def test_result_and_manifest_are_strict_json(injected_ports):
    """Manifests are serialized by storage_backend._json_bytes via json.dumps with the
    default allow_nan=True, which emits bare NaN/Infinity tokens and produces a file strict
    JSON readers reject. Every new float must be finite-or-null before it gets there."""
    _reader, store = injected_ports
    n = 20
    df = pd.DataFrame(
        {
            **_meta(n),
            "exact_a": [float(i) for i in range(n)],  # r == 1.0 -> null CI
            "exact_b": [float(i) * 3 + 1 for i in range(n)],
            "noisy": [float((i * 7) % 11) for i in range(n)],
        }
    )
    result = _run_with_frame(df, store=store)

    json.loads(result.model_dump_json(), parse_constant=_reject_constant)
    stored = store.get_run(_EXPERIMENT, "correlation_matrix", "latest")
    json.loads(json.dumps(stored.params), parse_constant=_reject_constant)


def test_strong_pairs_empty_when_nothing_is_strong(injected_ports):
    n = 24
    df = pd.DataFrame(
        {
            **_meta(n),
            "flat": [float(i % 4) for i in range(n)],
            "saw": [float((i * 5) % 7) for i in range(n)],
        }
    )
    assert not _strong_pairs_oracle(df), "fixture must contain no strong pair"
    result = _run_with_frame(df)

    assert result.strong_correlation_pairs == []
    assert result.strong_positive_correlations == 0
    assert result.strong_negative_correlations == 0
    assert result.strong_pair_overlap_min is None
    assert result.strong_pair_overlap_median is None
    assert result.strong_pair_overlap_max is None


def test_strong_pairs_ordering_is_deterministic(injected_ports):
    """The tool declares no random_state; ties in overlap_n must resolve the same way every
    run (descending |r|, then trait order) or snapshot consumers see spurious churn."""
    df = _collinear_frame(n_traits=8, n_rows=40, seed=11)
    first = _run_with_frame(df)
    second = _run_with_frame(df)

    as_tuples = lambda res: [  # noqa: E731 - local, single-expression
        (tuple(p.traits), p.r, p.overlap_n) for p in res.strong_correlation_pairs
    ]
    assert as_tuples(first) == as_tuples(second)
    # Ties in overlap_n resolve by descending |r|.
    by_overlap: dict[int, list[float]] = {}
    for p in first.strong_correlation_pairs:
        by_overlap.setdefault(p.overlap_n, []).append(abs(p.r))
    assert any(len(v) > 1 for v in by_overlap.values()), "fixture must contain a tie"
    for magnitudes in by_overlap.values():
        assert magnitudes == sorted(magnitudes, reverse=True)


# ── #785: the third blank-cell bucket ───────────────────────────────────────────────


def _locally_constant_frame(n: int = 30) -> pd.DataFrame:
    """`lc_b` is constant (7.0) on exactly the rows where `lc_a` is non-null, but varies
    elsewhere — so both traits are globally non-constant, the overlap (15) clears the floor,
    and Pearson r is still NaN. Verified against the pinned pandas."""
    half = n // 2
    return pd.DataFrame(
        {
            **_meta(n),
            "lc_a": [float(i) for i in range(half)] + [None] * (n - half),
            "lc_b": [7.0] * half + [float(i) for i in range(n - half)],
        }
    )


def test_locally_constant_pair_is_named(injected_ports):
    """#785: a pair can be globally non-constant AND clear the overlap floor and still be
    locally constant within the shared overlap — a blank cell named by neither existing
    disclosure list."""
    df = _locally_constant_frame()
    # Fixture self-check: the degeneracy is real and is not one of the other two causes.
    assert df["lc_a"].std() > 0 and df["lc_b"].std() > 0
    assert len(df[["lc_a", "lc_b"]].dropna()) >= 10
    assert pd.isna(df[["lc_a", "lc_b"]].corr().loc["lc_a", "lc_b"])

    result = _run_with_frame(df)

    assert ["lc_a", "lc_b"] in result.locally_constant_trait_pairs
    assert result.zero_variance_traits == []
    assert result.low_overlap_trait_pairs == []
    assert result.strong_positive_correlations == 0
    assert result.strong_negative_correlations == 0


def test_every_nan_cell_has_exactly_one_reason(injected_ports):
    """The taxonomy-totality property (#785): with all three causes present at once, every
    off-diagonal NaN cell is claimed by exactly one bucket — none unexplained, none twice.
    """
    n = 30
    half = n // 2
    df = pd.DataFrame(
        {
            **_meta(n),
            "ok_a": [float(i) for i in range(n)],
            "ok_b": [float((i * 3) % 11) for i in range(n)],
            "constant": [1.0] * n,  # -> zero_variance_traits
            "sparse_a": [float(i) if i < 10 else None for i in range(n)],
            "sparse_b": [
                float(i) if i >= 8 else None for i in range(n)
            ],  # -> low overlap
            "lc_a": [float(i) for i in range(half)] + [None] * (n - half),
            "lc_b": [7.0] * half
            + [float(i) for i in range(n - half)],  # -> locally const
        }
    )
    result = _run_with_frame(df)
    assert result.zero_variance_traits
    assert result.low_overlap_trait_pairs
    assert result.locally_constant_trait_pairs

    traits = result.resolved_trait_columns
    corr = (
        df[traits]
        .corr(min_periods=plot_correlation_matrix_tool._MIN_CORR_OVERLAP)
        .to_numpy()
    )
    zero_variance = set(result.zero_variance_traits)
    low = {tuple(p) for p in result.low_overlap_trait_pairs}
    locally_constant = {tuple(p) for p in result.locally_constant_trait_pairs}

    for i in range(len(traits)):
        for j in range(i + 1, len(traits)):
            if not np.isnan(corr[i, j]):
                continue
            pair = (traits[i], traits[j])
            claims = [
                traits[i] in zero_variance or traits[j] in zero_variance,
                pair in low,
                pair in locally_constant,
            ]
            assert sum(claims) == 1, f"{pair} claimed by {sum(claims)} buckets"


def test_low_overlap_and_zero_variance_pair_lands_in_one_bucket(injected_ports):
    """low_overlap_trait_pairs already drops pairs involving a zero-variance trait, so the
    residual derivation must subtract the raw sub-threshold MASK, not that published list —
    subtracting the list would leak such a pair into the new bucket (design.md Decision 1).
    """
    n = 30
    df = pd.DataFrame(
        {
            **_meta(n),
            # Constant AND observed on only 3 rows: both degenerate at once.
            "constant_sparse": [5.0, 5.0, 5.0] + [None] * (n - 3),
            "dense_a": [float(i) for i in range(n)],
            "dense_b": [float((i * 2) % 13) for i in range(n)],
        }
    )
    result = _run_with_frame(df)

    assert "constant_sparse" in result.zero_variance_traits
    for pair in result.low_overlap_trait_pairs + result.locally_constant_trait_pairs:
        assert "constant_sparse" not in pair


def test_locally_constant_capped_with_uncapped_count(injected_ports):
    """This bucket's population is independent of the other two: one 'saturating' trait is
    locally constant against every partner while low_overlap_trait_pairs stays empty, so the
    list needs its own cap and its true size has to survive it (design.md Risks)."""
    cap = plot_correlation_matrix_tool._MAX_LOCALLY_CONSTANT_PAIRS_REPORTED
    n_rows, n_partners = 40, cap + 12
    rng = np.random.default_rng(5)
    data = dict(_meta(n_rows))
    # Observed on rows 0..n-2 with a single value; its only variation is on the last row,
    # which every partner leaves null — so it is globally non-constant but locally constant
    # against all of them.
    saturating = [4.0] * (n_rows - 1) + [99.0]
    data["saturating"] = saturating
    for t in range(n_partners):
        values = rng.normal(size=n_rows)
        values[-1] = np.nan
        data[f"p{t}"] = values
    result = _run_with_frame(pd.DataFrame(data))

    assert result.zero_variance_traits == []
    assert result.low_overlap_trait_pairs == []
    assert result.locally_constant_pair_count == n_partners
    assert len(result.locally_constant_trait_pairs) == cap


def test_locally_constant_does_not_populate_heatmap_caveat(injected_ports, monkeypatch):
    """heatmap_caveat warns about cells the image COLORS confidently despite thin support.
    A locally-constant pair is NaN in the delegate's own unguarded corr too, so it renders
    blank and that text would be false of it (design.md Decision 4)."""
    captured = {}
    real = plot_correlation_matrix_tool.create_correlation_heatmap

    def _spy(*a, **k):
        fig = real(*a, **k)
        captured["fig"] = fig
        return fig

    monkeypatch.setattr(
        plot_correlation_matrix_tool, "create_correlation_heatmap", _spy
    )
    result = _run_with_frame(_locally_constant_frame())

    assert result.locally_constant_trait_pairs
    assert result.zero_variance_traits == []
    assert result.low_overlap_trait_pairs == []
    assert result.heatmap_caveat is None
    assert captured["fig"].texts == []


def test_locally_constant_pairs_survive_a_manifest_json_round_trip(injected_ports):
    """Same list[list[str]] shape contract the low-overlap pairs carry — a tuple would come
    back from JSON as a list and silently break equality for a manifest reader."""
    _reader, store = injected_ports
    result = _run_with_frame(_locally_constant_frame(), store=store)
    assert result.locally_constant_trait_pairs

    stored = store.get_run(_EXPERIMENT, "correlation_matrix", "latest")
    round_tripped = json.loads(
        json.dumps(stored.params["locally_constant_trait_pairs"])
    )
    assert round_tripped == stored.params["locally_constant_trait_pairs"]
    assert all(isinstance(pair, list) for pair in round_tripped)


def test_non_finite_trait_is_reported_as_zero_variance(injected_ports):
    """A column carrying +/-inf has a NaN standard deviation, so `not (std > 0)` already
    files it here — but the field described only three cases, telling a scientist with an inf
    that their trait is constant. Pins the corrected taxonomy (design.md Decision 7)."""
    n = 20
    df = pd.DataFrame(
        {
            **_meta(n),
            "has_inf": [float(i) for i in range(n - 1)] + [np.inf],
            "dense_a": [float(i) for i in range(n)],
            "dense_b": [float((i * 3) % 7) for i in range(n)],
        }
    )
    result = _run_with_frame(df)

    assert "has_inf" in result.zero_variance_traits
    for pair in result.low_overlap_trait_pairs + result.locally_constant_trait_pairs:
        assert "has_inf" not in pair
    # The half this test used to be missing (#784 review). Filing the trait under
    # zero_variance_traits is not sufficient on its own: pandas' nancorr masks with
    # np.isfinite, so it DROPS the inf row and returns an ordinary coefficient over the
    # remaining 19 — r = 1.0 here, which cleared the magnitude cutoff and was published as a
    # strong correlation for a trait the same response called uncorrelatable.
    for pair in result.strong_correlation_pairs:
        assert "has_inf" not in pair.traits
    assert result.strong_positive_correlations == _independent_strong_counts(df)[0]
    assert result.strong_negative_correlations == _independent_strong_counts(df)[1]


def _independent_strong_counts(df: pd.DataFrame) -> tuple[int, int]:
    """Strong counts re-derived from pandas directly, excluding degenerate traits.

    Deliberately not a call into the module's own masks — an oracle that shares the
    implementation's arithmetic cannot contradict it.
    """
    traits = [c for c in df.columns if c not in _META_COLS]
    std = df[traits].std(skipna=True)
    usable = [c for c in traits if 0 < std[c] < np.inf]
    corr = df[usable].corr(min_periods=plot_correlation_matrix_tool._MIN_CORR_OVERLAP)
    values = corr.to_numpy()
    upper = np.triu(np.ones(values.shape, dtype=bool), k=1)
    cutoff = plot_correlation_matrix_tool._STRONG_R
    return (
        int(((values > cutoff) & upper).sum()),
        int(((values < -cutoff) & upper).sum()),
    )


def test_no_non_finite_column_escapes_the_variance_guard(injected_ports):
    """The invariant that makes overlap_counts' isfinite/notna choice unobservable TODAY.

    pandas masks each pair with np.isfinite, so an overlap counted with notna over-reports
    the sample a coefficient rests on and feeds an inflated n to the Fisher interval
    (#784 review, B2). The implementation counts with isfinite for that reason — but with
    the zero-variance guard in place that fix is currently defense-in-depth, not an
    observable behaviour change, because EVERY non-finite-carrying column has a NaN or
    infinite std and is therefore excluded from the counts, the pair list and the
    locally-constant bucket alike. For any surviving pair, notna and isfinite agree.

    This test pins that reasoning rather than the unobservable difference. If it ever fails,
    the guard has been relaxed to let a non-finite column through — at which point
    overlap_counts' mask choice becomes load-bearing and needs its own direct coverage.
    """
    n = 20
    shapes = {
        "one_pos_inf": [float(i) for i in range(n - 1)] + [np.inf],
        "one_neg_inf": [float(i) for i in range(n - 1)] + [-np.inf],
        "both_infs": [float(i) for i in range(n - 2)] + [np.inf, -np.inf],
        "inf_and_nan": [float(i) for i in range(n - 2)] + [np.inf, None],
    }
    for name, values in shapes.items():
        df = pd.DataFrame(
            {
                **_meta(n),
                name: values,
                "anchor_a": [float(i) for i in range(n)],
                "anchor_b": [float(i) * 1.7 + (i % 3) for i in range(n)],
            }
        )
        result = _run_with_frame(df)
        assert name in result.zero_variance_traits, (
            f"{name} carries a non-finite value but escaped the guard; overlap_counts' "
            f"isfinite mask is now observable and needs direct coverage"
        )
        for pair in result.strong_correlation_pairs:
            assert name not in pair.traits
        for pair in (
            result.low_overlap_trait_pairs + result.locally_constant_trait_pairs
        ):
            assert name not in pair


def test_overflowing_variance_is_not_called_locally_constant(injected_ports):
    """#784 review (Important 2): a column of finite-but-enormous values overflows its sum of
    squares, so std is +inf and `inf > 0` waved it through as healthy. pandas then returned
    NaN for its coefficients and the pair was filed as 'locally constant' — which asserts the
    opposite of the actual problem, about two traits that are in fact perfectly correlated.
    """
    rng = np.random.default_rng(3)
    n = 20
    base = rng.normal(size=n)
    df = pd.DataFrame(
        {
            **_meta(n),
            "huge_a": base * 1e200,
            "huge_b": base * 2e200,  # exactly collinear with huge_a
            "ok_a": rng.normal(size=n),
            "ok_b": rng.normal(size=n),
        }
    )
    # Precondition: every value is finite, so this is NOT the inf-carrying case.
    traits = ["huge_a", "huge_b", "ok_a", "ok_b"]
    assert np.isfinite(df[traits].to_numpy()).all()

    result = _run_with_frame(df)

    assert set(result.zero_variance_traits) == {"huge_a", "huge_b"}
    for pair in result.locally_constant_trait_pairs:
        assert "huge_a" not in pair and "huge_b" not in pair


def test_min_corr_overlap_is_owned_not_aliased():
    """#784: the floor is a pairwise degeneracy guard; _CANONICAL_MIN_SAMPLES_PER_TRAIT is a
    per-trait completeness convention. They agree at 10 by coincidence, so a QC-side retune
    must not silently move which pairs this tool counts and flags.

    Deliberately does NOT assert the two constants are still equal: that equality is the
    coincidence being decoupled, and pinning it would turn the next legitimate QC retune into
    a test failure whose cheapest fix is to re-alias (design.md Decision 5).
    """
    assert plot_correlation_matrix_tool._MIN_CORR_OVERLAP == 10
    # Deliberately NOT `assert not hasattr(module, "_CANONICAL_MIN_SAMPLES_PER_TRAIT")`
    # (#784 review): that is a name-binding check, not a behaviour one. It passes anyway
    # under `from bloom_mcp.tools import _qc_shared`, and it would FAIL if someone imported
    # the symbol for a legitimate reason — e.g. to warn when the two constants diverge.
    # The value assertion above is what actually pins the decoupling.


def test_new_disclosure_fields_stamped_into_manifest_params(injected_ports):
    """A later manifest reader must recover the same evidence a live caller got — the same
    contract resolved_trait_columns/heatmap_caveat already carry."""
    _reader, store = injected_ports
    n = 30
    half = n // 2
    df = pd.DataFrame(
        {
            **_meta(n),
            "full_a": [float(i) for i in range(n)],
            "full_b": [float(i) * 2 + (i % 3) for i in range(n)],
            "lc_a": [float(i) for i in range(half)] + [None] * (n - half),
            "lc_b": [7.0] * half + [float(i) for i in range(n - half)],
        }
    )
    result = _run_with_frame(df, store=store)
    assert result.strong_correlation_pairs
    assert result.locally_constant_trait_pairs

    params = store.get_run(_EXPERIMENT, "correlation_matrix", "latest").params
    assert params["locally_constant_trait_pairs"] == result.locally_constant_trait_pairs
    assert params["locally_constant_pair_count"] == result.locally_constant_pair_count
    assert params["strong_pair_overlap_min"] == result.strong_pair_overlap_min
    assert params["strong_pair_overlap_median"] == result.strong_pair_overlap_median
    assert params["strong_pair_overlap_max"] == result.strong_pair_overlap_max
    assert params["strong_correlation_pairs"] == [
        p.model_dump() for p in result.strong_correlation_pairs
    ]


def test_locally_constant_pairs_are_really_locally_constant(injected_ports):
    """#784 review (B3): the label must be checked, not just the partition.

    ``locally_constant_trait_pairs`` is derived by ELIMINATION, so a test that re-derives it
    the same way cannot fail. This one calls the tool and then establishes the label
    independently: for every reported pair, restrict both traits to the rows where BOTH are
    finite (pandas' own mask) and assert at least one of them really does take a single
    distinct value there. ``nunique()`` shares no arithmetic with the implementation's
    ``isnan``-on-the-guarded-matrix derivation.
    """
    n = 30
    half = n // 2
    df = pd.DataFrame(
        {
            **_meta(n),
            "ok_a": [float(i) for i in range(n)],
            "ok_b": [float((i * 3) % 11) for i in range(n)],
            "lc_a": [float(i) for i in range(half)] + [None] * (n - half),
            "lc_b": [7.0] * half + [float(i) for i in range(n - half)],
        }
    )
    result = _run_with_frame(df)
    assert result.locally_constant_trait_pairs, "fixture must produce the bucket"

    for a, b in result.locally_constant_trait_pairs:
        shared = df[[a, b]][np.isfinite(df[a]) & np.isfinite(df[b])]
        assert len(shared) >= plot_correlation_matrix_tool._MIN_CORR_OVERLAP, (
            f"{a}x{b} is below the overlap floor, so it belongs in "
            f"low_overlap_trait_pairs, not this bucket"
        )
        assert shared[a].nunique() == 1 or shared[b].nunique() == 1, (
            f"{a}x{b} was labelled locally constant, but both traits vary across their "
            f"{len(shared)} shared finite rows "
            f"(nunique: {shared[a].nunique()}, {shared[b].nunique()})"
        )


@pytest.mark.parametrize("seed", range(12))
def test_taxonomy_totality_over_randomly_degenerate_frames(injected_ports, seed):
    """Totality (#785) over frames built to be degenerate in randomly-varied ways.

    Unlike the fuzz this replaces, the buckets come from the TOOL's response, and the NaN
    cells come from an independent pandas call on the same frame — so sabotaging any bucket
    makes this fail rather than silently re-partitioning.
    """
    rng = np.random.default_rng(seed)
    n = 30
    cols: dict[str, list] = {}
    for k in range(rng.integers(4, 8)):
        kind = rng.integers(0, 4)
        if kind == 0:  # dense
            cols[f"t{k}"] = [float(rng.normal()) for _ in range(n)]
        elif kind == 1:  # constant -> zero variance
            cols[f"t{k}"] = [3.0] * n
        elif kind == 2:  # sparse -> low overlap against other sparse columns
            start = int(rng.integers(0, n - 6))
            cols[f"t{k}"] = [
                float(rng.normal()) if start <= i < start + 6 else None
                for i in range(n)
            ]
        else:  # constant where observed -> locally constant
            half = int(rng.integers(8, n - 4))
            cols[f"t{k}"] = [5.0] * half + [None] * (n - half)
    # Two dense anchors guarantee the >=2-usable-trait precondition.
    cols["anchor_a"] = [float(i) for i in range(n)]
    cols["anchor_b"] = [float(i) * 1.7 + (i % 3) for i in range(n)]
    df = pd.DataFrame({**_meta(n), **cols})

    result = _run_with_frame(df)
    traits = result.resolved_trait_columns
    corr = (
        df[traits]
        .corr(min_periods=plot_correlation_matrix_tool._MIN_CORR_OVERLAP)
        .to_numpy()
    )
    zero_variance = set(result.zero_variance_traits)
    low = {tuple(p) for p in result.low_overlap_trait_pairs}
    locally_constant = {tuple(p) for p in result.locally_constant_trait_pairs}

    unexplained, double_counted = [], []
    for i in range(len(traits)):
        for j in range(i + 1, len(traits)):
            if not np.isnan(corr[i, j]):
                continue
            pair = (traits[i], traits[j])
            claims = sum(
                (
                    traits[i] in zero_variance or traits[j] in zero_variance,
                    pair in low,
                    pair in locally_constant,
                )
            )
            if claims == 0:
                unexplained.append(pair)
            elif claims > 1:
                double_counted.append(pair)
    assert not unexplained, f"NaN cells in no bucket: {unexplained}"
    assert not double_counted, f"NaN cells in >1 bucket: {double_counted}"


def test_counts_and_pair_list_share_one_cutoff(injected_ports):
    """#784 review (Important 1): the cutoff was written inline at three sites, so the counts
    and the list that claims to explain them could drift apart under an edit to one of them.

    Pins the relationship, not the literal: strong_pair_count must equal the two counts
    summed, and every reported pair must clear the module's own constant. A `>` -> `>=`
    slip in the pair mask alone now fails here.
    """
    n = 40
    rng = np.random.default_rng(11)
    base = rng.normal(size=n)
    df = pd.DataFrame(
        {
            **_meta(n),
            "a": base,
            "b": base * 2 + rng.normal(size=n) * 0.05,  # strongly positive
            "c": -base * 3 + rng.normal(size=n) * 0.05,  # strongly negative
            "d": rng.normal(size=n),  # unrelated
        }
    )
    result = _run_with_frame(df)
    cutoff = plot_correlation_matrix_tool._STRONG_R

    assert result.strong_pair_count == (
        result.strong_positive_correlations + result.strong_negative_correlations
    )
    assert result.strong_pair_count == len(_strong_pairs_oracle(df))
    for pair in result.strong_correlation_pairs:
        assert abs(pair.r) > cutoff


def test_locally_constant_cap_order_is_pinned(injected_ports):
    """#784 review (Important 3): the cap slices np.where output, and nothing pinned which 20
    survived — taking the LAST 20 instead of the first passed the whole suite.

    The order is resolved_trait_columns order (row-major over the upper triangle). It is
    deterministic but arbitrary, which the field description says; this pins that it is at
    least the documented one.
    """
    n = 30
    half = n // 2
    cap = plot_correlation_matrix_tool._MAX_LOCALLY_CONSTANT_PAIRS_REPORTED
    # One trait constant wherever observed is locally constant against EVERY partner, so
    # this yields far more than the cap.
    # Each t-column is observed only in the first half; "saturating" is constant (7.0)
    # exactly there and varies afterwards, so it is globally non-constant, clears the
    # overlap floor against every t-column, and is locally constant against all of them.
    cols = {
        f"t{k:02d}": [float((i * (k + 1)) % 17) if i < half else None for i in range(n)]
        for k in range(cap + 8)
    }
    cols["saturating"] = [7.0] * half + [float(i) for i in range(n - half)]
    df = pd.DataFrame({**_meta(n), **cols})

    result = _run_with_frame(df)
    assert result.locally_constant_pair_count > cap
    assert len(result.locally_constant_trait_pairs) == cap

    traits = result.resolved_trait_columns
    corr = (
        df[traits]
        .corr(min_periods=plot_correlation_matrix_tool._MIN_CORR_OVERLAP)
        .to_numpy()
    )
    zero_variance = set(result.zero_variance_traits)
    low = {tuple(p) for p in result.low_overlap_trait_pairs}
    expected = []
    for i in range(len(traits)):
        for j in range(i + 1, len(traits)):
            pair = (traits[i], traits[j])
            if (
                np.isnan(corr[i, j])
                and traits[i] not in zero_variance
                and traits[j] not in zero_variance
                and pair not in low
            ):
                expected.append([traits[i], traits[j]])
    assert result.locally_constant_trait_pairs == expected[:cap]


def test_assumption_violated_names_every_case_it_files(injected_ports):
    """#784 review (Important 4): the error said 'constant or entirely NaN' about a set that
    also contains inf-carrying and variance-overflowing traits — neither of which is either.
    """
    n = 20
    df = pd.DataFrame(
        {
            **_meta(n),
            "has_inf": [np.inf] + [float(i) for i in range(1, n)],
            "constant": [4.0] * n,
            "ok": [float(i) for i in range(n)],
        }
    )
    with pytest.raises(BloomMCPError) as excinfo:
        _run_with_frame(df)
    message = str(excinfo.value)
    assert "has_inf" in message
    # The message must not assert the two things that are false of has_inf.
    assert "are constant or entirely NaN" not in message
    for phrase in ("non-finite", "overflow"):
        assert phrase in message


def test_run_parameters_are_stamped_for_later_comparability(injected_ports):
    """#784 review (Important 6): the constants block argues that owning _MIN_CORR_OVERLAP
    matters because a retune would move the counts and break comparability with persisted
    manifests — which only holds if the manifest records what it ran under. Two manifests
    produced under different floors were previously indistinguishable.
    """
    _reader, store = injected_ports
    n = 30
    df = pd.DataFrame(
        {
            **_meta(n),
            "a": [float(i) for i in range(n)],
            "b": [float(i) * 2 + (i % 3) for i in range(n)],
        }
    )
    _run_with_frame(df, store=store)
    params = store.get_run(_EXPERIMENT, "correlation_matrix", "latest").params

    assert params["min_corr_overlap"] == plot_correlation_matrix_tool._MIN_CORR_OVERLAP
    assert params["strong_r_cutoff"] == plot_correlation_matrix_tool._STRONG_R
    assert params["ci_level"] == plot_correlation_matrix_tool._CI_LEVEL
    assert (
        params["max_strong_pairs_reported"]
        == plot_correlation_matrix_tool._MAX_STRONG_PAIRS_REPORTED
    )
    assert (
        params["max_locally_constant_pairs_reported"]
        == plot_correlation_matrix_tool._MAX_LOCALLY_CONSTANT_PAIRS_REPORTED
    )


def test_manifest_keeps_locally_constant_uncapped_and_flags_strong_truncation(
    injected_ports,
):
    """#784 review (Important 5): the new lists were stamped CAPPED, inverting this file's
    own test-enforced precedent (test_full_uncapped_disclosure_lists_are_recoverable_from_
    the_manifest) and leaving pairs 21..N recoverable from nowhere.

    locally_constant_trait_pairs is a name list, so it is restored to uncapped like its two
    siblings. strong_correlation_pairs stays capped — each entry is a four-field record, not
    a name — but its uncapped MAGNITUDES are now stamped beside it, so a manifest-only reader
    can tell the list is truncated and by how much. That asymmetry is the deliberate part.
    """
    _reader, store = injected_ports
    n = 30
    half = n // 2
    cap = plot_correlation_matrix_tool._MAX_LOCALLY_CONSTANT_PAIRS_REPORTED
    # Each t-column is observed only in the first half; "saturating" is constant (7.0)
    # exactly there and varies afterwards, so it is globally non-constant, clears the
    # overlap floor against every t-column, and is locally constant against all of them.
    cols = {
        f"t{k:02d}": [float((i * (k + 1)) % 17) if i < half else None for i in range(n)]
        for k in range(cap + 8)
    }
    cols["saturating"] = [7.0] * half + [float(i) for i in range(n - half)]
    df = pd.DataFrame({**_meta(n), **cols})

    result = _run_with_frame(df, store=store)
    params = store.get_run(_EXPERIMENT, "correlation_matrix", "latest").params

    assert result.locally_constant_pair_count > cap
    assert len(result.locally_constant_trait_pairs) == cap
    stamped = params["locally_constant_trait_pairs"]
    assert len(stamped) == result.locally_constant_pair_count
    assert [list(p) for p in result.locally_constant_trait_pairs] == [
        list(p) for p in stamped[:cap]
    ]

    # The strong side: capped list, but truncation is detectable from the manifest alone.
    assert params["strong_pair_count"] == result.strong_pair_count
    assert params["strong_positive_correlations"] == result.strong_positive_correlations
    assert params["strong_negative_correlations"] == result.strong_negative_correlations
    assert len(params["strong_correlation_pairs"]) == min(
        result.strong_pair_count,
        plot_correlation_matrix_tool._MAX_STRONG_PAIRS_REPORTED,
    )


def test_fisher_ci_oracle_is_independent_of_the_module_constant(injected_ports):
    """Suggestion (#784 review): the existing closed-form test re-types the module's own
    critical value, so a typo in both places is invisible. NormalDist().inv_cdf is an
    independent source for the same quantity.
    """
    from statistics import NormalDist

    z_independent = NormalDist().inv_cdf(0.975)
    assert plot_correlation_matrix_tool._Z_CRIT == pytest.approx(
        z_independent, abs=1e-15
    )
    assert plot_correlation_matrix_tool._CI_LEVEL == 0.95

    r, n_overlap = 0.7, 10
    lo, hi = plot_correlation_matrix_tool._fisher_ci(r, n_overlap)
    z = np.arctanh(r)
    half = z_independent / np.sqrt(n_overlap - 3)
    assert lo == pytest.approx(float(np.tanh(z - half)), rel=1e-12)
    assert hi == pytest.approx(float(np.tanh(z + half)), rel=1e-12)


def test_fisher_ci_at_the_smallest_defined_overlap():
    """Suggestion (#784 review): n=4 is the first overlap with a defined interval and by far
    the widest, and it was untested — the existing sub-floor test only covered n=3."""
    lo, hi = plot_correlation_matrix_tool._fisher_ci(0.7, 4)
    assert lo is not None and hi is not None
    assert lo < -0.7 and hi > 0.98  # essentially uninformative, as it should be
    assert plot_correlation_matrix_tool._fisher_ci(0.7, 3) == (None, None)


def test_field_descriptions_record_the_taxonomy_they_promise():
    """Suggestion (#784 review): the spec requires these descriptions to name specific cases,
    but only the behaviour was asserted — a future edit deleting the sentences kept the suite
    green.
    """
    fields = PlotCorrelationMatrixResult.model_fields
    zero_variance = fields["zero_variance_traits"].description
    for phrase in ("inf", "overflow", "underflow"):
        assert phrase in zero_variance, f"{phrase!r} missing from zero_variance_traits"

    ci_low = (
        fields["strong_correlation_pairs"]
        .annotation.__args__[0]
        .model_fields["ci_low"]
        .description
    )
    # The two limits a scientist would otherwise act on unknowingly.
    assert "independent" in ci_low.lower()
    assert "selected" in ci_low.lower()

    locally_constant = fields["locally_constant_trait_pairs"].description
    assert "arbitrary" in locally_constant.lower()


@pytest.mark.parametrize("n_overlap, expect_reported", [(10, True), (9, False)])
def test_strong_pair_boundary_at_min_periods(
    injected_ports, n_overlap, expect_reported
):
    """#833 review round 2: the 9-vs-10 boundary was pinned only for the sibling
    `low_overlap_trait_pairs` list, never for the strong-pair/CI path this change adds.

    At exactly the floor the pair must be reported WITH a defined interval computed at that
    same n; one row below it, pandas returns NaN and the pair must vanish from the counts,
    the list and the summaries alike. An off-by-one here would publish a Fisher interval for
    an overlap the tool's own floor rejects.
    """
    assert plot_correlation_matrix_tool._MIN_CORR_OVERLAP == 10
    n = 24
    # Perfectly-but-not-exactly collinear over the observed rows, so |r| clears the cutoff
    # without hitting 1.0 (which would legitimately null the interval and mask the boundary).
    df = pd.DataFrame(
        {
            **_meta(n),
            "pair_a": [float(i) if i < n_overlap else None for i in range(n)],
            "pair_b": [
                float(i) * 2.0 + (0.3 if i % 3 == 0 else 0.0) if i < n_overlap else None
                for i in range(n)
            ],
            "anchor_a": [float((i * 5) % 13) for i in range(n)],
            "anchor_b": [float((i * 7) % 11) for i in range(n)],
        }
    )
    result = _run_with_frame(df)

    reported = {tuple(p.traits): p for p in result.strong_correlation_pairs}.get(
        ("pair_a", "pair_b")
    )
    assert (reported is not None) is expect_reported

    if expect_reported:
        assert reported.overlap_n == n_overlap
        expected = plot_correlation_matrix_tool._fisher_ci(reported.r, n_overlap)
        assert (reported.ci_low, reported.ci_high) == expected
        assert reported.ci_low is not None
    else:
        # Below the floor the pair is NaN, so it reaches neither count nor summary.
        assert ["pair_a", "pair_b"] in result.low_overlap_trait_pairs


def test_new_machinery_at_the_two_trait_minimum(injected_ports):
    """#833 review round 2: the sort/CI/cap path was never exercised at the 2-column
    minimum the tool accepts, where the upper triangle holds exactly one cell.

    Degenerate shapes are where an `np.where` / `lexsort` / slice pipeline tends to break
    (a scalar where an array is expected, an empty-axis reduction), and every list here is
    length 0 or 1.
    """
    n = 20
    df = pd.DataFrame(
        {
            **_meta(n),
            "only_a": [float(i) for i in range(n)],
            "only_b": [float(i) * 3.0 + (i % 4) for i in range(n)],
        }
    )
    result = _run_with_frame(df)

    assert len(result.resolved_trait_columns) == 2
    assert result.strong_pair_count == (
        result.strong_positive_correlations + result.strong_negative_correlations
    )
    assert result.strong_pair_count == 1
    assert len(result.strong_correlation_pairs) == 1

    only = result.strong_correlation_pairs[0]
    assert only.traits == ["only_a", "only_b"]
    assert only.overlap_n == n
    # The three uncapped summaries must agree with the single pair they summarise.
    assert (
        result.strong_pair_overlap_min
        == result.strong_pair_overlap_max
        == only.overlap_n
    )
    assert result.strong_pair_overlap_median == float(only.overlap_n)
    assert result.locally_constant_trait_pairs == []
    assert result.locally_constant_pair_count == 0


def test_every_off_diagonal_cell_is_locally_constant(injected_ports):
    """#833 review round 2: no fixture drove the WHOLE off-diagonal matrix into the new
    bucket, so the saturated case — where the taxonomy's third list explains every cell and
    the other two are empty — went unexercised.

    Every trait is observed only on the first half of the rows and takes a single value
    there, while varying globally on the second half. So every pair clears the overlap floor,
    no trait is globally constant, and every coefficient is NaN.
    """
    n = 30
    half = n // 2
    n_traits = 5
    cols = {
        f"sat_{k}": [float(k + 1)] * half
        + [float(i * (k + 2)) for i in range(n - half)]
        for k in range(n_traits)
    }
    # Each trait is observed everywhere, but pairwise they are constant across the first
    # half; the differing tails are what keeps them globally non-constant.
    for k in range(n_traits):
        col = cols[f"sat_{k}"]
        for i in range(half, n):
            if i % n_traits != k:
                col[i] = None
    df = pd.DataFrame({**_meta(n), **cols})
    result = _run_with_frame(df)

    traits = result.resolved_trait_columns
    expected_pairs = len(traits) * (len(traits) - 1) // 2
    assert result.zero_variance_traits == []
    assert result.low_overlap_trait_pairs == []
    assert result.locally_constant_pair_count == expected_pairs
    assert result.strong_pair_count == 0
    assert result.strong_correlation_pairs == []
    assert result.strong_pair_overlap_min is None
    assert result.strong_pair_overlap_median is None
    assert result.strong_pair_overlap_max is None
    # And the label still holds for every one of them.
    for a, b in result.locally_constant_trait_pairs:
        shared = df[[a, b]][np.isfinite(df[a]) & np.isfinite(df[b])]
        assert shared[a].nunique() == 1 or shared[b].nunique() == 1
