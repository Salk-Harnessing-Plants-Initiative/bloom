"""Contract + oracle tests for the granular ``cross_experiment_correlations`` tool (#489).

bloommcp's first two-experiment-input consumer. Around the 5 standard contract patterns
(schema round-trip, no-leak, require_clean, persistence, deterministic-no-seed) this file
covers what's specific to a dual-input tool: independent per-experiment validation
(cleaned version, genotype column, trait selection, finiteness), the composite
``experiment``/``based_on_version`` encoding (design.md D1) — including the reject-outright
guard against a filename stem that would collide or truncate under ``AnalysisDir``'s
re-applied ``Path(...).stem`` (two review rounds found this: first a naive un-sanitized
join, then a lossy dot-to-underscore sanitization that reopened the same collision one
level down) — the reserved-`correlation` tool_class reuse (design.md D9), and — the
north-star oracle — the confirmed upstream ``min_samples`` no-op and bloommcp's
pre-filter workaround (design.md D8, talmolab/sleap-roots-analyze#205), reproduced
against the real turface_19/cylinder fixture pair via the recorded golden.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.tools import _ports
from bloom_mcp.sections.sleap_roots.analysis import (
    cross_experiment_correlations as xcorr_tool,
)
from bloom_mcp.sections.sleap_roots.analysis.cross_experiment_correlations import (
    CrossExperimentCorrelationsParams,
    CrossExperimentCorrelationsResult,
    _COMPOSITE_SEPARATOR,
    cross_experiment_correlations,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_GOLDEN = json.loads(
    (_FIXTURES / "turface_cylinder_cross_experiment_correlation_golden.json").read_text(
        encoding="utf-8"
    )
)
_TOL = 1e-6

_EXP_1 = "expA.csv"
_EXP_2 = "expB.csv"


def _correlated_pair() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Two small, strongly-correlated experiments: 3 shared genotypes, 3 reps each."""
    df1 = pd.DataFrame(
        {
            "Genotype": ["G1", "G1", "G1", "G2", "G2", "G2", "G3", "G3", "G3"],
            "Barcode": [f"A{i}" for i in range(9)],
            "TraitA1": [1.0, 1.1, 0.9, 2.0, 2.1, 1.9, 3.0, 3.1, 2.9],
        }
    )
    df2 = pd.DataFrame(
        {
            "Genotype": ["G1", "G1", "G1", "G2", "G2", "G2", "G3", "G3", "G3"],
            "Barcode": [f"B{i}" for i in range(9)],
            "TraitB1": [100.0, 102.0, 98.0, 200.0, 205.0, 195.0, 300.0, 295.0, 305.0],
        }
    )
    return df1, df2


def _uncorrelated_pair() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Genotype-mean traits with real variance but only a weak (r~0.33) relationship —
    for the zero-significant case. Deliberately not near-constant (a near-constant
    genotype mean triggers scipy's ConstantInputWarning / an undefined correlation —
    see test_constant_genotype_mean_trait_yields_nan_correlation_not_a_crash for that
    case exercised deliberately)."""
    df1 = pd.DataFrame(
        {
            "Genotype": ["G1", "G1", "G1", "G2", "G2", "G2", "G3", "G3", "G3"],
            "TraitA1": [4.0, 5.0, 6.0, 8.0, 9.0, 10.0, 6.0, 7.0, 8.0],
        }
    )
    df2 = pd.DataFrame(
        {
            "Genotype": ["G1", "G1", "G1", "G2", "G2", "G2", "G3", "G3", "G3"],
            "TraitB1": [9.0, 3.0, 15.0, 2.0, 20.0, 6.0, 11.0, 1.0, 18.0],
        }
    )
    return df1, df2


@pytest.fixture
def injected_ports():
    reader = FakeReader()
    store = FakeResultStore()
    df1, df2 = _correlated_pair()
    reader.add_cleaned_version(_EXP_1, "v1", df1, make_latest=True)
    reader.add_cleaned_version(_EXP_2, "v1", df2, make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _run(**overrides) -> CrossExperimentCorrelationsResult:
    params = {
        "experiment_1": _EXP_1,
        "experiment_2": _EXP_2,
        "trait_columns_1": ["TraitA1"],
        "trait_columns_2": ["TraitB1"],
        **overrides,
    }
    return cross_experiment_correlations(CrossExperimentCorrelationsParams(**params))


def _captured_outputs(store, monkeypatch, run_fn) -> dict[str, str]:
    """Run once, returning every committed output's staged text keyed by filename.

    ``FakeResultStore.commit`` tears down the staging dir on success (mirroring the
    real adapter's ephemeral local staging), so outputs must be captured at commit
    time — mirrors ``test_clustering_tool.py``'s ``_labels_of`` helper.
    """
    holder: dict[str, str] = {}
    real_commit = store.commit

    def _commit(run, outputs):
        for name in outputs:
            holder[name] = (run.staging_dir / name).read_text(encoding="utf-8")
        return real_commit(run, outputs)

    monkeypatch.setattr(store, "commit", _commit)
    run_fn()
    return holder


# ── delegation, no reimplementation ──────────────────────────────────────────


def test_delegates_to_upstream_correlation_chain(injected_ports, monkeypatch):
    calls: dict[str, int] = {}

    def _wrap(name, real):
        def _spy(*a, **k):
            calls[name] = calls.get(name, 0) + 1
            return real(*a, **k)

        return _spy

    monkeypatch.setattr(
        xcorr_tool,
        "calculate_genotype_means",
        _wrap("means", xcorr_tool.calculate_genotype_means),
    )
    monkeypatch.setattr(
        xcorr_tool,
        "calculate_cross_experiment_correlations",
        _wrap("corr", xcorr_tool.calculate_cross_experiment_correlations),
    )
    monkeypatch.setattr(
        xcorr_tool,
        "identify_significant_correlations",
        _wrap("sig", xcorr_tool.identify_significant_correlations),
    )
    monkeypatch.setattr(
        xcorr_tool,
        "summarize_correlation_results",
        _wrap("summary", xcorr_tool.summarize_correlation_results),
    )
    _run()
    assert calls["means"] == 2  # once per experiment
    assert calls["corr"] == 1
    assert calls["sig"] == 1
    assert calls["summary"] == 1


def test_load_and_align_experiments_never_called(injected_ports, monkeypatch):
    import sleap_roots_analyze

    def _boom(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("load_and_align_experiments must never be called")

    monkeypatch.setattr(sleap_roots_analyze, "load_and_align_experiments", _boom)
    _run()  # must not raise


def test_upstream_min_samples_no_op_still_present():
    """Regression pin for design.md D8 / talmolab/sleap-roots-analyze#205: calls the
    RAW upstream delegate directly (bypassing bloommcp entirely) and asserts the
    no-op is still present. If upstream ever fixes it, this test fails LOUDLY,
    signaling that bloommcp's pre-filter workaround should be reconsidered/removed
    rather than silently going stale alongside a fixed dependency."""
    from sleap_roots_analyze import calculate_cross_experiment_correlations

    exp1_means = pd.DataFrame(
        {"t1": [1.0, 2.0, 3.0], "n_samples": [10, 10, 1]}, index=["G1", "G2", "G3"]
    )
    exp2_means = pd.DataFrame(
        {"t2": [1.0, 2.0, 3.0], "n_samples": [10, 10, 1]}, index=["G1", "G2", "G3"]
    )
    # G3 has only 1 sample; min_samples=5 SHOULD exclude it if actually enforced.
    result = calculate_cross_experiment_correlations(
        exp1_means, exp2_means, ["t1"], ["t2"], min_samples=5
    )
    assert int(result["n_genotypes"].iloc[0]) == 3, (
        "upstream's min_samples filtering appears to have been fixed in "
        "sleap_roots_analyze -- re-evaluate design.md D8's bloommcp-side pre-filter "
        "workaround (talmolab/sleap-roots-analyze#205); it may now be redundant"
    )


def test_upstream_min_samples_no_op_still_present_on_real_fixture_pair():
    """Same regression pin as test_upstream_min_samples_no_op_still_present, but against
    the REAL turface_19/cylinder genotype-means data and the recorded golden's
    min_samples_3_upstream_no_op block, rather than a synthetic 3-genotype fixture
    (found in review — the golden fixture was generated recording this exact scenario,
    but no test actually read that block back until now, leaving it orphaned)."""
    from sleap_roots_analyze import (
        calculate_cross_experiment_correlations,
        calculate_genotype_means,
    )

    turface = pd.read_csv(_FIXTURES / "turface_19_final_data.csv")
    cylinder = pd.read_csv(_FIXTURES / "cylinder_final_data.csv")
    gm1 = calculate_genotype_means(
        turface, [_GOLDEN["trait_1"]], genotype_col=_GOLDEN["genotype_col"]
    )
    gm2 = calculate_genotype_means(
        cylinder, [_GOLDEN["trait_2"]], genotype_col=_GOLDEN["genotype_col"]
    )

    # Calling the RAW upstream delegate directly (bypassing bloommcp's pre-filter
    # workaround entirely) with min_samples=3: cylinder's GH_7371 has n_samples=2 and
    # SHOULD be excluded if min_samples were actually enforced upstream.
    result = calculate_cross_experiment_correlations(
        gm1, gm2, [_GOLDEN["trait_1"]], [_GOLDEN["trait_2"]], min_samples=3
    )
    g = _GOLDEN["min_samples_3_upstream_no_op"]
    assert int(result["n_genotypes"].iloc[0]) == g["n_genotypes"], (
        "upstream's min_samples filtering appears to have been fixed in "
        "sleap_roots_analyze -- re-evaluate design.md D8's bloommcp-side pre-filter "
        "workaround (talmolab/sleap-roots-analyze#205); it may now be redundant"
    )
    assert result["correlation"].iloc[0] == pytest.approx(g["correlation"], abs=_TOL)


# ── min_samples pre-filter (north-star oracle: the confirmed upstream bug) ──────


def test_min_samples_prefilter_actually_excludes_under_replicated_genotypes(
    monkeypatch,
):
    """Reproduces the confirmed upstream no-op (talmolab/sleap-roots-analyze#205) and
    proves bloommcp's pre-filter workaround (design.md D8) actually excludes the
    under-replicated genotype, on the real turface_19/cylinder fixture pair."""
    reader = FakeReader()
    store = FakeResultStore()
    turface = pd.read_csv(_FIXTURES / "turface_19_final_data.csv")
    cylinder = pd.read_csv(_FIXTURES / "cylinder_final_data.csv")
    reader.add_cleaned_version("turface_19.csv", "v1", turface, make_latest=True)
    reader.add_cleaned_version("cylinder.csv", "v1", cylinder, make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:

        def _call():
            return cross_experiment_correlations(
                CrossExperimentCorrelationsParams(
                    experiment_1="turface_19.csv",
                    experiment_2="cylinder.csv",
                    trait_columns_1=[_GOLDEN["trait_1"]],
                    trait_columns_2=[_GOLDEN["trait_2"]],
                    min_samples=3,
                    r_threshold=0.0,
                )
            )

        outputs = _captured_outputs(store, monkeypatch, _call)
        g = _GOLDEN["min_samples_3_bloommcp_prefiltered"]
        corr_csv = pd.read_csv(io.StringIO(outputs["correlations.csv"]))
        assert int(corr_csv["n_genotypes"].iloc[0]) == g["n_genotypes"]
        assert corr_csv["correlation"].iloc[0] == pytest.approx(
            g["correlation"], abs=_TOL
        )
        # excluded genotype must not appear in either persisted genotype-means table
        gm2 = pd.read_csv(io.StringIO(outputs["genotype_means_2.csv"]))
        assert g["excluded_genotype"] not in gm2["Genotype"].tolist()
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


# ── require cleaned input on both sides ──────────────────────────────────────


def test_requires_cleaned_version_both_experiments():
    reader = FakeReader()
    store = FakeResultStore()
    df1, df2 = _correlated_pair()
    reader.add_experiment(_EXP_1, df1)  # raw only, no cleaned version
    reader.add_cleaned_version(_EXP_2, "v1", df2, make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            _run()
        assert exc.value.code == "tool_error"
        assert _EXP_1 in exc.value.message
        assert "qc_clean" in exc.value.remedy

        # symmetric: experiment_2 missing instead
        reader2 = FakeReader()
        reader2.add_cleaned_version(_EXP_1, "v1", df1, make_latest=True)
        reader2.add_experiment(_EXP_2, df2)
        _ports.configure(reader=reader2, store=store)
        with pytest.raises(BloomMCPError) as exc2:
            _run()
        assert exc2.value.code == "tool_error"
        assert _EXP_2 in exc2.value.message
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_two_cleaned_experiments_consumed_reports_sources(injected_ports):
    result = _run()
    assert result.source_1 == "v1_cleaned"
    assert result.source_2 == "v1_cleaned"


# ── required genotype column on both sides (genuinely symmetric) ────────────────


@pytest.mark.parametrize(
    "offending",
    [pytest.param(_EXP_1, id="experiment_1"), pytest.param(_EXP_2, id="experiment_2")],
)
def test_missing_genotype_role_either_side_rejected(offending):
    """Parametrized (found in review — a prior manual for-loop over both directions
    would mask a failure on the first case, hiding a regression in the second; matches
    this file's other genuinely-symmetric tests' parametrize style)."""
    df1, df2 = _correlated_pair()
    bad_df1 = df1.drop(columns=["Genotype"]) if offending == _EXP_1 else df1
    bad_df2 = df2.drop(columns=["Genotype"]) if offending == _EXP_2 else df2

    reader = FakeReader()
    store = FakeResultStore()
    reader.add_cleaned_version(_EXP_1, "v1", bad_df1, make_latest=True)
    reader.add_cleaned_version(_EXP_2, "v1", bad_df2, make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            cross_experiment_correlations(
                CrossExperimentCorrelationsParams(
                    experiment_1=_EXP_1, experiment_2=_EXP_2
                )
            )
        assert exc.value.code == "assumption_violated"
        assert offending in exc.value.message
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


# ── finite-value defense-in-depth (genuinely symmetric) ─────────────────────────


@pytest.mark.parametrize(
    "offending",
    [pytest.param(_EXP_1, id="experiment_1"), pytest.param(_EXP_2, id="experiment_2")],
)
def test_non_finite_value_either_side_rejected(offending):
    """Parametrized (found in review — see test_missing_genotype_role_either_side_rejected
    for why a manual for-loop over both directions is worse here)."""
    df1, df2 = _correlated_pair()
    if offending == _EXP_1:
        df1["TraitA1"] = [float("inf")] + list(df1["TraitA1"][1:])
    else:
        df2["TraitB1"] = [float("inf")] + list(df2["TraitB1"][1:])

    reader = FakeReader()
    store = FakeResultStore()
    reader.add_cleaned_version(_EXP_1, "v1", df1, make_latest=True)
    reader.add_cleaned_version(_EXP_2, "v1", df2, make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            _run()
        assert exc.value.code == "assumption_violated"
        assert offending in exc.value.message
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


@pytest.mark.filterwarnings("ignore:.*constant.*:UserWarning")
def test_constant_genotype_mean_trait_yields_nan_correlation_not_a_crash(monkeypatch):
    """design.md's Risks section accepts that a constant (zero-variance) genotype-mean
    trait can yield a NaN correlation row that passes through into correlations.csv
    rather than being rejected. This proves that acceptance path is actually exercised
    (not just theorized): the persisted correlations.csv row is itself confirmed NaN
    (found in review — a prior version of this test only inferred the NaN indirectly
    via n_significant/n_highly_significant counts, never reading the persisted file),
    and the NaN row is correctly excluded from n_significant/n_highly_significant (NaN
    comparisons are always False in pandas), not silently corrupting either count."""
    reader = FakeReader()
    store = FakeResultStore()
    df1 = pd.DataFrame(
        {
            "Genotype": ["G1", "G1", "G1", "G2", "G2", "G2", "G3", "G3", "G3"],
            "TraitA1": [5.0] * 9,  # constant -> constant genotype means -> NaN r
        }
    )
    df2 = pd.DataFrame(
        {
            "Genotype": ["G1", "G1", "G1", "G2", "G2", "G2", "G3", "G3", "G3"],
            "TraitB1": [100.0, 102.0, 98.0, 200.0, 205.0, 195.0, 300.0, 295.0, 305.0],
        }
    )
    reader.add_cleaned_version(_EXP_1, "v1", df1, make_latest=True)
    reader.add_cleaned_version(_EXP_2, "v1", df2, make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        captured: dict[str, str] = {}
        real_commit = store.commit

        def _commit(run, outputs):
            for name in outputs:
                captured[name] = (run.staging_dir / name).read_text(encoding="utf-8")
            return real_commit(run, outputs)

        monkeypatch.setattr(store, "commit", _commit)
        result = cross_experiment_correlations(
            CrossExperimentCorrelationsParams(
                experiment_1=_EXP_1,
                experiment_2=_EXP_2,
                trait_columns_1=["TraitA1"],
                trait_columns_2=["TraitB1"],
                r_threshold=0.0,
            )
        )

        corr_csv = pd.read_csv(io.StringIO(captured["correlations.csv"]))
        assert pd.isna(corr_csv["correlation"].iloc[0])
        assert pd.isna(corr_csv["p_value"].iloc[0])
        assert result.n_correlations == 1
        assert result.n_significant == 0
        assert result.n_highly_significant == 0
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


# ── degenerate vs. empty-but-valid ───────────────────────────────────────────


def test_zero_correlations_is_degenerate_input():
    reader = FakeReader()
    store = FakeResultStore()
    df1, df2 = _correlated_pair()
    df2 = df2.copy()
    df2["Genotype"] = ["H1", "H1", "H1", "H2", "H2", "H2", "H3", "H3", "H3"]
    reader.add_cleaned_version(_EXP_1, "v1", df1, make_latest=True)
    reader.add_cleaned_version(_EXP_2, "v1", df2, make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            _run()
        assert exc.value.code == "assumption_violated"
        assert store.list_runs("expA__x__expB", "correlation") == []
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_exactly_one_shared_genotype_is_degenerate():
    """Correlation is mathematically undefined at n=1; the delegate's own hardcoded
    <3-genotype floor makes this indistinguishable from zero shared genotypes, so the
    same assumption_violated path fires."""
    reader = FakeReader()
    store = FakeResultStore()
    df1 = pd.DataFrame({"Genotype": ["G1", "G1", "G1"], "TraitA1": [1.0, 2.0, 3.0]})
    df2 = pd.DataFrame({"Genotype": ["G1", "G1", "G1"], "TraitB1": [10.0, 20.0, 30.0]})
    reader.add_cleaned_version(_EXP_1, "v1", df1, make_latest=True)
    reader.add_cleaned_version(_EXP_2, "v1", df2, make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            cross_experiment_correlations(
                CrossExperimentCorrelationsParams(
                    experiment_1=_EXP_1,
                    experiment_2=_EXP_2,
                    trait_columns_1=["TraitA1"],
                    trait_columns_2=["TraitB1"],
                    min_samples=1,
                )
            )
        assert exc.value.code == "assumption_violated"
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_zero_significant_is_not_an_error():
    reader = FakeReader()
    store = FakeResultStore()
    df1, df2 = _uncorrelated_pair()
    reader.add_cleaned_version(_EXP_1, "v1", df1, make_latest=True)
    reader.add_cleaned_version(_EXP_2, "v1", df2, make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        result = cross_experiment_correlations(
            CrossExperimentCorrelationsParams(
                experiment_1=_EXP_1,
                experiment_2=_EXP_2,
                trait_columns_1=["TraitA1"],
                trait_columns_2=["TraitB1"],
                r_threshold=0.9,
            )
        )
        assert result.n_significant == 0
        assert result.n_correlations == 1
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


# ── trait selection ───────────────────────────────────────────────────────────


def test_default_trait_selection_uses_full_certified_set_both_sides(injected_ports):
    reader, _store = injected_ports
    frame1 = reader.load_experiment(_EXP_1, require_clean=True)
    frame2 = reader.load_experiment(_EXP_2, require_clean=True)
    result = cross_experiment_correlations(
        CrossExperimentCorrelationsParams(experiment_1=_EXP_1, experiment_2=_EXP_2)
    )
    assert result.n_traits_1 == len(frame1.trait_cols)
    assert result.n_traits_2 == len(frame2.trait_cols)


@pytest.mark.parametrize(
    "kwargs,needle",
    [
        ({"trait_columns_1": ["NoSuchTrait"]}, "NoSuchTrait"),
        ({"trait_columns_2": ["NoSuchTrait"]}, "NoSuchTrait"),
        ({"trait_columns_1": []}, _EXP_1),
        ({"trait_columns_2": []}, _EXP_2),
        ({"trait_columns_1": ["TraitA1", "TraitA1"]}, "TraitA1"),
        ({"trait_columns_2": ["TraitB1", "TraitB1"]}, "TraitB1"),
    ],
)
def test_trait_columns_validated_independently(injected_ports, kwargs, needle):
    with pytest.raises(BloomMCPError) as exc:
        _run(**kwargs)
    assert exc.value.code == "invalid_input"
    assert needle in exc.value.message


def test_non_numeric_trait_column_names_experiment(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns_1=["Barcode"])
    assert exc.value.code == "invalid_input"
    assert _EXP_1 in exc.value.message


# ── deterministic, no seed ────────────────────────────────────────────────────


def test_seed_recorded_as_none(injected_ports):
    _reader, store = injected_ports
    _run()
    stored = store.get_run("expA__x__expB", "correlation", "latest")
    assert stored.seed is None


def test_repeated_runs_identical(injected_ports):
    first = _run()
    second = _run()
    assert first.n_correlations == second.n_correlations
    assert first.n_significant == second.n_significant
    assert first.n_highly_significant == second.n_highly_significant


# ── composite experiment/based_on_version + reserved-character/self/path guards ──


def test_run_persisted_with_composite_experiment_and_based_on_version(
    injected_ports, monkeypatch
):
    _reader, store = injected_ports
    captured: dict[str, object] = {}
    real_create = store.create_run

    def _spy(**kwargs):
        captured.update(kwargs)
        return real_create(**kwargs)

    monkeypatch.setattr(store, "create_run", _spy)
    _run()
    assert captured["experiment"] == "expA__x__expB"
    assert (
        captured["provenance"].based_on_version
        == f"{_EXP_1}@v1_cleaned|{_EXP_2}@v1_cleaned"
    )


@pytest.mark.parametrize("bad_field", ["experiment_1", "experiment_2"])
def test_reserved_encoding_character_in_filename_rejected(injected_ports, bad_field):
    kwargs = {
        "experiment_1": _EXP_1,
        "experiment_2": _EXP_2,
        "trait_columns_1": ["TraitA1"],
        "trait_columns_2": ["TraitB1"],
    }
    kwargs[bad_field] = kwargs[bad_field].replace(".csv", "@bad|.csv")
    with pytest.raises(BloomMCPError) as exc:
        cross_experiment_correlations(CrossExperimentCorrelationsParams(**kwargs))
    assert exc.value.code == "invalid_input"
    assert bad_field in exc.value.message


def test_self_correlation_rejected():
    """experiment_1 == experiment_2 is rejected up front, not silently computed as a
    meaningless self-vs-self correlation matrix (found in review)."""
    with pytest.raises(BloomMCPError) as exc:
        cross_experiment_correlations(
            CrossExperimentCorrelationsParams(experiment_1=_EXP_1, experiment_2=_EXP_1)
        )
    assert exc.value.code == "invalid_input"
    assert _EXP_1 in exc.value.message


@pytest.mark.parametrize("bad_field", ["experiment_1", "experiment_2"])
def test_path_unsafe_experiment_name_rejected(bad_field):
    """Explicit path-traversal guard (defense-in-depth, found in review — this tool's
    safety was previously incidental, not an explicit check). The message must name
    which of experiment_1/experiment_2 was rejected (found in a second review pass —
    _validate_experiment_name's message previously never named the offending field, so
    a two-name caller couldn't tell which one failed)."""
    kwargs = {"experiment_1": _EXP_1, "experiment_2": _EXP_2}
    kwargs[bad_field] = "../secrets/passwd.csv"
    with pytest.raises(BloomMCPError) as exc:
        cross_experiment_correlations(CrossExperimentCorrelationsParams(**kwargs))
    assert exc.value.code == "invalid_input"
    assert bad_field in exc.value.message


@pytest.mark.parametrize("bad_field", ["experiment_1", "experiment_2"])
@pytest.mark.parametrize(
    "unsafe_name",
    [
        pytest.param("my.experiment.v2.csv", id="internal-dot"),
        pytest.param("control__x__treatment.csv", id="embeds-separator"),
    ],
)
def test_unsafe_composite_stem_rejected(bad_field, unsafe_name):
    """Regression test for the composite-key collision bug found in review — TWICE:
    a naive f"{Path(e1).stem}__x__{Path(e2).stem}" composite is silently truncated by
    AnalysisDir's own re-applied Path(...).stem whenever either original stem contains
    a dot (e.g. "my.experiment.v2.csv" -> stem "my.experiment.v2"), losing the other
    experiment's name and risking a storage collision. A first fix sanitized dots to
    underscores before joining, which reopened the identical collision class one level
    down ("my.experiment.csv" and "my_experiment.csv" both sanitize to "my_experiment").
    A stem containing the separator itself has the same failure mode from the other
    direction: two distinct (experiment_1, experiment_2) pairs can join to the same
    composite string. All three are now rejected outright rather than sanitized."""
    kwargs = {"experiment_1": _EXP_1, "experiment_2": _EXP_2}
    kwargs[bad_field] = unsafe_name
    with pytest.raises(BloomMCPError) as exc:
        cross_experiment_correlations(CrossExperimentCorrelationsParams(**kwargs))
    assert exc.value.code == "invalid_input"
    assert bad_field in exc.value.message


def test_analysis_dir_preserves_composite_key_for_safe_stems():
    """Directly exercises the REAL AnalysisDir (not FakeResultStore, whose simplified
    stem helper cannot reproduce the original truncation bug — see design.md D1)
    against this tool's actual (un-sanitized, since unsafe stems are now rejected
    outright by _reject_unsafe_composite_stem rather than reaching this construction)
    composite-key construction."""
    from bloom_mcp.manifest.analysis_dir import AnalysisDir

    e1, e2 = "turface_19.csv", "cylinder.csv"
    composite = f"{Path(e1).stem}{_COMPOSITE_SEPARATOR}{Path(e2).stem}"
    adir = AnalysisDir("bloommcp_output", composite, "correlation")
    assert adir.stem == composite
    assert "turface_19" in adir.stem
    assert "cylinder" in adir.stem


# ── content-addressing + genotype-means artifacts ────────────────────────────


def test_source_csv_content_addresses_both_inputs(injected_ports):
    _reader, store = injected_ports

    def _hash_for(vary_exp1: float | None, vary_exp2: float | None) -> str:
        reader = FakeReader()
        df1, df2 = _correlated_pair()
        if vary_exp1 is not None:
            df1.loc[0, "TraitA1"] = vary_exp1
        if vary_exp2 is not None:
            df2.loc[0, "TraitB1"] = vary_exp2
        reader.add_cleaned_version(_EXP_1, "v1", df1, make_latest=True)
        reader.add_cleaned_version(_EXP_2, "v1", df2, make_latest=True)
        _ports.configure(reader=reader, store=store)
        _run()
        stored = store.get_run("expA__x__expB", "correlation", "latest")
        return stored.output_sha256["correlations.csv"]

    baseline = _hash_for(None, None)
    assert (
        _hash_for(999.0, None) != baseline
    ), "experiment_1 alone should change the hash"
    assert (
        _hash_for(None, 999.0) != baseline
    ), "experiment_2 alone should change the hash"


def test_genotype_means_artifacts_persisted(injected_ports):
    _reader, store = injected_ports
    _run()
    stored = store.get_run("expA__x__expB", "correlation", "latest")
    assert "genotype_means_1.csv" in stored.output_keys
    assert "genotype_means_2.csv" in stored.output_keys


# ── summary serializable, no full matrix inline ──────────────────────────────


def test_summary_json_serializable_no_numpy_leaks(injected_ports, monkeypatch):
    _reader, store = injected_ports
    captured: dict[str, str] = {}
    real_commit = store.commit

    def _commit(run, outputs):
        captured["summary"] = (run.staging_dir / "summary.json").read_text(
            encoding="utf-8"
        )
        return real_commit(run, outputs)

    monkeypatch.setattr(store, "commit", _commit)
    _run()
    parsed = json.loads(captured["summary"])  # must not raise
    assert isinstance(parsed["total_correlations"], int)


def test_result_never_inlines_full_correlation_matrix(injected_ports):
    result = _run()
    dumped = result.model_dump()
    assert not any(isinstance(v, list) and len(v) > 20 for v in dumped.values())


# ── discoverable via list_existing_analyses (reused correlation slot) ────────


def test_discoverable_via_list_existing_analyses(injected_ports):
    from bloom_mcp.sections.core.list_existing_analyses import (
        TOOL_CLASSES,
        list_existing_analyses,
    )

    assert "correlation" in TOOL_CLASSES
    _run()
    response = json.loads(list_existing_analyses("expA__x__expB"))
    assert "correlation" in response["analyses"]


# ── schema round-trip + out-of-range params ──────────────────────────────────


def test_schema_round_trip(injected_ports):
    result = _run()
    again = CrossExperimentCorrelationsResult.model_validate(
        json.loads(result.model_dump_json())
    )
    assert again.n_correlations == result.n_correlations


@pytest.mark.parametrize(
    "bad_kwargs",
    [
        {"min_samples": 0},
        {"p_threshold": 1.5},
        {"p_threshold": -0.1},
        {"r_threshold": 1.5},
        {"r_threshold": -0.1},
    ],
)
def test_out_of_range_parameters_rejected(injected_ports, bad_kwargs):
    with pytest.raises(BloomMCPError) as exc:
        cross_experiment_correlations(
            {
                "experiment_1": _EXP_1,
                "experiment_2": _EXP_2,
                "trait_columns_1": ["TraitA1"],
                "trait_columns_2": ["TraitB1"],
                **bad_kwargs,
            }
        )
    assert exc.value.code == "invalid_input"


# ── no leaked internals ───────────────────────────────────────────────────────


def test_no_error_leaks_backend_internals(injected_ports, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(xcorr_tool, "calculate_genotype_means", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "/var" not in msg and "db.internal" not in msg


# ── tools/list presence ───────────────────────────────────────────────────────


def test_cross_experiment_correlations_in_tools_list():
    import asyncio
    from fastmcp import Client
    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "sleap_roots_cross_experiment_correlations" in tools
    assert tools["sleap_roots_cross_experiment_correlations"].inputSchema is not None


# ── numeric oracle against the real turface_19/cylinder golden ──────────────


def test_reproduces_golden_correlation_unfiltered(monkeypatch):
    reader = FakeReader()
    store = FakeResultStore()
    turface = pd.read_csv(_FIXTURES / "turface_19_final_data.csv")
    cylinder = pd.read_csv(_FIXTURES / "cylinder_final_data.csv")
    reader.add_cleaned_version("turface_19.csv", "v1", turface, make_latest=True)
    reader.add_cleaned_version("cylinder.csv", "v1", cylinder, make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:

        def _call():
            return cross_experiment_correlations(
                CrossExperimentCorrelationsParams(
                    experiment_1="turface_19.csv",
                    experiment_2="cylinder.csv",
                    trait_columns_1=[_GOLDEN["trait_1"]],
                    trait_columns_2=[_GOLDEN["trait_2"]],
                    min_samples=1,
                    r_threshold=0.0,
                )
            )

        outputs = _captured_outputs(store, monkeypatch, _call)
        g = _GOLDEN["unfiltered"]
        corr_csv = pd.read_csv(io.StringIO(outputs["correlations.csv"]))
        assert int(corr_csv["n_genotypes"].iloc[0]) == g["n_genotypes"]
        assert corr_csv["correlation"].iloc[0] == pytest.approx(
            g["correlation"], abs=_TOL
        )
        assert corr_csv["p_value"].iloc[0] == pytest.approx(g["p_value"], abs=_TOL)
        assert bool(corr_csv["significant"].iloc[0]) == g["significant"]
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
