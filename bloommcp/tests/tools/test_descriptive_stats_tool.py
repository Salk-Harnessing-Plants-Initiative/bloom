"""Contract + golden tests for the granular ``descriptive_stats`` tool (#488).

The simplest granular consumer: reads a *cleaned* experiment (``require_clean=True``),
restricts to the certified-clean trait set, delegates ALL computation to
``sleap_roots_analyze.calculate_trait_statistics`` in one call, and persists a versioned
``stats.csv`` under tool class ``stats``. No method surface, no seed, no plots.

Unlike the PCA/clustering/heritability goldens (characterization snapshots with no
external ground truth on turface_19), ``calculate_trait_statistics`` computes
parameter-free textbook arithmetic — the recorded golden is independently hand-verifiable
from the raw CSV, not merely a drift gate.
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
    descriptive_stats as descriptive_stats_tool,
)
from bloom_mcp.sections.sleap_roots.analysis.descriptive_stats import (
    DescriptiveStatsParams,
    DescriptiveStatsResult,
    descriptive_stats,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_raw_data.csv"
_GOLDEN = json.loads(
    (_FIXTURES / "turface_19_stats_golden.json").read_text(encoding="utf-8")
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

    Uses the tested upstream ``clean_traits_for_analysis`` (not the code under test),
    exactly as ``qc_clean`` would produce it — same recipe the stats golden was computed
    from (see ``tests/fixtures/README.md``).
    """
    raw = pd.read_csv(_RAW, encoding="utf-8")
    det = detect_columns(raw)
    cleaned, _kept, _log = clean_traits_for_analysis(
        raw, trait_cols=det["trait_cols"], **_roles(det)
    )
    return cleaned


@pytest.fixture
def injected_ports():
    """FakeReader serving the canonical-default cleaned turface_19 frame + FakeResultStore."""
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_cleaned_version(_EXPERIMENT, "v1", _cleaned_df(), make_latest=True)
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _run(**overrides) -> DescriptiveStatsResult:
    params = {"experiment": _EXPERIMENT, **overrides}
    return descriptive_stats(DescriptiveStatsParams(**params))


def _stat(result: DescriptiveStatsResult, trait: str):
    return next(s for s in result.stats_per_trait if s.trait == trait)


# ── 2. Golden stats through the tool (north star) ───────────────────────────


def test_golden_stats_through_the_tool(injected_ports):
    """2.2 — reproduce the independently-computed golden through the MCP tool, for
    EVERY one of the golden's 19 recorded traits (not just a couple of samples) — a
    column-ordering or single-stat mapping bug anywhere in the 19 would otherwise go
    undetected."""
    result = _run()

    assert set(_GOLDEN["stats_per_trait"]) == {s.trait for s in result.stats_per_trait}
    for trait, golden in _GOLDEN["stats_per_trait"].items():
        s = _stat(result, trait)
        assert s.n == golden["count"] == 158, trait
        assert s.mean == pytest.approx(golden["mean"], abs=1e-9), trait
        assert s.std == pytest.approx(golden["std"], abs=1e-9), trait
        assert s.median == pytest.approx(golden["median"], abs=1e-9), trait
        assert s.q25 == pytest.approx(golden["q25"], abs=1e-9), trait
        assert s.q75 == pytest.approx(golden["q75"], abs=1e-9), trait
        assert s.min == pytest.approx(golden["min"], abs=1e-9), trait
        assert s.max == pytest.approx(golden["max"], abs=1e-9), trait
        assert s.cv == pytest.approx(golden["cv"], abs=1e-9), trait
        assert s.skewness == pytest.approx(golden["skewness"], abs=1e-6), trait
        assert s.kurtosis == pytest.approx(golden["kurtosis"], abs=1e-6), trait

    # Root_Shoot_Ratio: deliberately non-normal — must not be clipped/flagged (folded
    # into the loop above too, called out here as the specific case that motivated it).
    rsr_golden = _GOLDEN["stats_per_trait"]["Root_Shoot_Ratio"]
    rsr = _stat(result, "Root_Shoot_Ratio")
    assert rsr.skewness == pytest.approx(rsr_golden["skewness"], abs=1e-6)
    assert rsr.kurtosis == pytest.approx(rsr_golden["kurtosis"], abs=1e-6)


def test_no_silent_sample_loss(injected_ports):
    """2.3 — every reported trait's n equals the full certified sample count."""
    result = _run()
    assert result.n_samples == 158
    for s in result.stats_per_trait:
        assert s.n == 158


def test_counts_are_consistent(injected_ports):
    """2.4 — n_traits_requested/reported/failed agree; nothing failed."""
    result = _run()
    assert result.n_traits_requested == result.n_traits_reported == 19
    assert result.n_failed == 0
    assert result.failed_traits == []


# ── 3.1 tools/list presence ──────────────────────────────────────────────────


def test_descriptive_stats_appears_in_tools_list():
    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "sleap_roots_descriptive_stats" in tools
    assert tools["sleap_roots_descriptive_stats"].inputSchema is not None


# ── 3.2 schema round-trip ────────────────────────────────────────────────────


def test_valid_input_output_round_trip(injected_ports):
    result = _run()
    again = DescriptiveStatsResult.model_validate(json.loads(result.model_dump_json()))
    assert again.n_traits_reported == result.n_traits_reported


def test_missing_experiment_is_invalid_input():
    with pytest.raises(BloomMCPError) as exc:
        descriptive_stats({})
    assert exc.value.code == "invalid_input"


# ── 3.3 provenance + links ───────────────────────────────────────────────────


def test_provenance_and_persisted_run(injected_ports, monkeypatch):
    _reader, store = injected_ports
    captured = {}
    real_create = store.create_run

    def _spy_create(**kwargs):
        captured["based_on_version"] = kwargs["provenance"].based_on_version
        return real_create(**kwargs)

    monkeypatch.setattr(store, "create_run", _spy_create)

    result = _run()

    stored = store.get_run(_EXPERIMENT, "stats", "latest")
    assert stored.tool == "descriptive_stats"
    assert stored.seed is None
    assert captured["based_on_version"] == "v1_cleaned" == result.source
    assert "stats.csv" in stored.output_keys
    assert result.outputs == dict(stored.output_keys)
    assert result.run_ref == stored.run_ref
    assert result.manifest_path == stored.manifest_path


def test_3_3b_source_label_and_bounded_payload(injected_ports):
    """3.3b — result.source is the resolved cleaned source, and no *other* field is
    huge. ``stats_per_trait`` is intentionally sized proportional to the (<=50-capped)
    trait count -- that is the summary, not a leak -- so it is checked separately
    against a generous per-cap bound rather than the blanket small-field threshold.
    """
    result = _run()
    assert result.source == "v1_cleaned"
    assert result.source != "raw"

    dumped = result.model_dump()
    other_fields = {k: v for k, v in dumped.items() if k != "stats_per_trait"}
    assert not any(
        isinstance(v, (list, dict)) and len(str(v)) > 5000
        for v in other_fields.values()
    )
    # 50 traits x a generous 1000 chars/entry ceiling -- catches an accidental
    # full-table leak (e.g. the cylinder cap silently not applying) without
    # penalizing the intended bounded summary.
    assert len(str(dumped["stats_per_trait"])) < 50 * 1000


# ── 3.3c user_label ───────────────────────────────────────────────────────────


def test_user_label_is_slugged_into_the_version_dir(injected_ports):
    """``user_label`` (zero prior coverage) reaches ``ResultStore.create_run`` and is
    slugified into the version directory name -- lowercased, spaces/punctuation
    stripped, exactly as ``manifest.versioning.slugify``/``version_dir_name`` define."""
    result = _run(user_label="My Run!")
    assert result.version_dir.endswith("_my_run")

    _reader, store = injected_ports
    stored = store.get_run(_EXPERIMENT, "stats", "latest")
    assert stored.version_dir == result.version_dir


def test_omitted_user_label_leaves_version_dir_unlabeled(injected_ports):
    """No ``user_label`` -> version_dir is just ``v<N>_<YYYY-MM-DD>``, no trailing
    slug segment (contrast the labeled case above)."""
    result = _run()
    assert result.version_dir.count("_") == 1


# ── 3.4 delegation pinning ───────────────────────────────────────────────────


def test_delegates_once_and_never_recomputes_a_statistic(injected_ports, monkeypatch):
    captured = {}
    real = descriptive_stats_tool.calculate_trait_statistics

    def _spy(df, trait_cols, **kwargs):
        captured["n_calls"] = captured.get("n_calls", 0) + 1
        captured["trait_cols"] = list(trait_cols)
        return real(df, trait_cols, **kwargs)

    monkeypatch.setattr(descriptive_stats_tool, "calculate_trait_statistics", _spy)

    result = _run()

    assert captured["n_calls"] == 1
    assert set(captured["trait_cols"]) == set(_GOLDEN["kept_trait_columns"])
    assert result.n_traits_reported == 19


# ── 3.5 guardrail — un-cleaned input ─────────────────────────────────────────


def test_raw_only_experiment_is_rejected_with_qc_clean_remedy():
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_experiment("rawonly.csv", _cleaned_df())  # raw only, no cleaned version
    _ports.configure(reader=reader, store=store)
    try:
        with pytest.raises(BloomMCPError) as exc:
            descriptive_stats(DescriptiveStatsParams(experiment="rawonly.csv"))
        # Genuinely mirrors pca_analysis/clustering's own handling of this exact
        # guard (both use code="tool_error") — NOT remove_outliers's
        # assumption_violated.
        assert exc.value.code == "tool_error"
        assert "qc_clean" in exc.value.remedy.lower()
        assert store.list_runs("rawonly.csv", "stats") == []
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


# ── 3.6 trait-selection validation ───────────────────────────────────────────


def test_unknown_trait_column_is_invalid_input_naming_it(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["NoSuchTrait"])
    assert exc.value.code == "invalid_input"
    assert "NoSuchTrait" in exc.value.message


def test_non_certified_numeric_column_is_rejected(injected_ports, monkeypatch):
    called = {"n": 0}

    def _spy(*a, **k):  # pragma: no cover - must not run
        called["n"] += 1
        raise AssertionError("delegate called with a non-certified column")

    monkeypatch.setattr(descriptive_stats_tool, "calculate_trait_statistics", _spy)

    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["Replicate"])
    assert exc.value.code == "invalid_input"
    assert called["n"] == 0


def test_non_numeric_identifier_column_is_rejected(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["Barcode"])
    assert exc.value.code == "invalid_input"
    assert "Barcode" in exc.value.message


def test_empty_trait_columns_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=[])
    assert exc.value.code == "invalid_input"


def test_duplicate_trait_columns_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=["Holes", "Holes"])
    assert exc.value.code == "invalid_input"


def test_explicit_valid_subset_narrows_stats_per_trait(injected_ports):
    result = _run(trait_columns=["Holes", "Surface.Area.mm2"])
    assert {s.trait for s in result.stats_per_trait} == {"Holes", "Surface.Area.mm2"}
    assert result.n_traits_requested == 2


# ── 3.7 determinism ───────────────────────────────────────────────────────────


def test_repeated_runs_are_identical(injected_ports):
    a = _run()
    b = _run()
    a_map = {s.trait: s.mean for s in a.stats_per_trait}
    b_map = {s.trait: s.mean for s in b.stats_per_trait}
    assert a_map == pytest.approx(b_map, abs=1e-9)


# ── 3.8 50-trait cap + omitted_traits ────────────────────────────────────────


def _wide_cleaned_df(n_traits: int, n_samples: int = 20) -> pd.DataFrame:
    data = {
        "Barcode": [f"p{i}" for i in range(n_samples)],
        "Genotype": (["A", "B"] * n_samples)[:n_samples],
        "Replicate": (list(range(1, 6)) * n_samples)[:n_samples],
    }
    for i in range(n_traits):
        data[f"trait_{i:03d}"] = [float(j + i) for j in range(n_samples)]
    return pd.DataFrame(data)


def test_wide_experiment_inline_summary_is_truncated(injected_ports):
    reader, _store = injected_ports
    reader.add_cleaned_version("wide.csv", "v1", _wide_cleaned_df(60), make_latest=True)

    result = descriptive_stats(DescriptiveStatsParams(experiment="wide.csv"))

    assert len(result.stats_per_trait) == 50
    assert result.truncated_in_summary is True
    expected_omitted = [f"trait_{i:03d}" for i in range(50, 60)]
    assert result.omitted_traits == expected_omitted

    stored = _store.get_run("wide.csv", "stats", "latest")
    assert stored is not None


def test_narrow_experiment_not_truncated(injected_ports):
    reader, _store = injected_ports
    reader.add_cleaned_version(
        "narrow.csv", "v1", _wide_cleaned_df(5), make_latest=True
    )

    result = descriptive_stats(DescriptiveStatsParams(experiment="narrow.csv"))

    assert len(result.stats_per_trait) == 5
    assert result.truncated_in_summary is False
    assert result.omitted_traits == []


# ── 3.9 non-finite coercion + nonfinite_stat_traits ──────────────────────────


def _nonfinite_cleaned_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Barcode": [f"p{i}" for i in range(10)],
            "Genotype": ["A"] * 5 + ["B"] * 5,
            "Replicate": [1, 2, 3, 4, 5] * 2,
            # Zero variance, non-zero mean -> skewness/kurtosis are nan (SciPy
            # divide-by-zero). Not reachable via real qc_clean output (it strips
            # zero-variance traits) -- a hand-crafted frame here bypasses that.
            "constant_trait": [7.0] * 10,
            # Non-zero variance, mean exactly 0 -> cv is inf. Genuinely reachable
            # through real qc_clean output (no cleanup step excludes this).
            "zero_mean_trait": [-2.0, -1.0, 0.0, 1.0, 2.0, -2.0, -1.0, 0.0, 1.0, 2.0],
            "normal_trait": [1.0, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        }
    )


def test_nonfinite_statistics_are_coerced_to_none_and_named(fake_supabase_storage):
    reader = FakeReader()
    reader.add_cleaned_version(
        "nonfinite.csv", "v1", _nonfinite_cleaned_df(), make_latest=True
    )
    store = SupabaseResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        result = descriptive_stats(DescriptiveStatsParams(experiment="nonfinite.csv"))

        const = _stat(result, "constant_trait")
        assert const.skewness is None
        assert const.kurtosis is None
        assert const.cv == pytest.approx(0.0, abs=1e-12)  # finite: mean=7 != 0

        zero_mean = _stat(result, "zero_mean_trait")
        assert zero_mean.cv is None
        assert zero_mean.skewness is not None  # finite for this trait

        normal = _stat(result, "normal_trait")
        assert normal.cv is not None and normal.skewness is not None

        assert set(result.nonfinite_stat_traits) == {
            "constant_trait",
            "zero_mean_trait",
        }

        # Persisted CSV agrees: empty cell, not a literal inf/nan token. Read the
        # ACTUAL committed bytes from the (fake, in-memory) object store via the real
        # SupabaseResultStore -- NOT FakeResultStore, whose commit() deletes the
        # staging dir and whose output_keys are logical strings, not real paths, so a
        # Path(csv_key).exists() check there is always False and silently no-ops this
        # assertion (confirmed: a prior version of this test used FakeResultStore and
        # never actually executed the inf/nan checks below).
        csv_key = result.outputs["stats.csv"]
        csv_text = fake_supabase_storage.objects[csv_key].decode("utf-8")
        assert "inf" not in csv_text.lower().replace("infinity", "")
        assert "nan" not in csv_text.lower()
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_near_zero_mean_produces_large_finite_cv_not_coerced(injected_ports):
    """A near-zero (but not exactly zero) mean is a known, recurring shape for
    zero-inflated trait data (e.g. cylinder). ``cv`` is large but finite in that case,
    so it must NOT be coerced to None or flagged in nonfinite_stat_traits -- coercion
    only fires for a genuinely non-finite (inf/nan) result, i.e. an exactly-zero mean,
    never merely a large one."""
    reader, _store = injected_ports
    near_zero_df = pd.DataFrame(
        {
            "Barcode": [f"p{i}" for i in range(6)],
            "Genotype": ["A", "A", "A", "B", "B", "B"],
            "Replicate": [1, 2, 3, 1, 2, 3],
            "near_zero_mean_trait": [-2.0, -1.0, 0.0, 1.0, 2.0, 0.001],
        }
    )
    reader.add_cleaned_version("nearzero.csv", "v1", near_zero_df, make_latest=True)

    result = descriptive_stats(DescriptiveStatsParams(experiment="nearzero.csv"))

    trait = _stat(result, "near_zero_mean_trait")
    assert trait.cv is not None
    assert abs(trait.cv) > 1000  # large but finite -- not coerced
    assert "near_zero_mean_trait" not in result.nonfinite_stat_traits


# ── 3.9b finiteness re-verification guard: per-trait, not all-or-nothing ────


def test_residual_nan_input_trait_fails_only_that_trait(monkeypatch):
    """A certified trait carrying a residual NaN (a reader/qc_clean-invariant
    violation) is excluded from delegation and reported in failed_traits -- but,
    unlike pca_analysis's all-or-nothing guard, must NOT block every other healthy
    trait in the same request (see the module docstring's rationale: per-trait stats
    have no cross-trait dependency)."""
    reader = FakeReader()
    store = FakeResultStore()
    bad_df = pd.DataFrame(
        {
            "Barcode": [f"p{i}" for i in range(6)],
            "Genotype": ["A", "A", "A", "B", "B", "B"],
            "Replicate": [1, 2, 3, 1, 2, 3],
            "trait_a": [1.0, 2.0, None, 4.0, 5.0, 6.0],
            "trait_b": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
        }
    )
    reader.add_cleaned_version("nanleak.csv", "v1", bad_df, make_latest=True)
    _ports.configure(reader=reader, store=store)

    captured = {}
    real = descriptive_stats_tool.calculate_trait_statistics

    def _spy(df, trait_cols, **kwargs):
        captured["trait_cols"] = list(trait_cols)
        return real(df, trait_cols, **kwargs)

    monkeypatch.setattr(descriptive_stats_tool, "calculate_trait_statistics", _spy)

    try:
        result = descriptive_stats(DescriptiveStatsParams(experiment="nanleak.csv"))
        assert result.failed_traits == ["trait_a"]
        assert result.n_failed == 1
        assert {s.trait for s in result.stats_per_trait} == {"trait_b"}
        # trait_a never reaches the delegate -- must never silently under-count n via
        # the delegate's own per-trait dropna().
        assert captured["trait_cols"] == ["trait_b"]
        # A run IS persisted now (unlike the old all-or-nothing guard, which raised
        # before ever calling store.create_run).
        assert store.get_run("nanleak.csv", "stats", "latest") is not None
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_all_traits_nonfinite_input_persists_empty_run_not_raise(monkeypatch):
    """Degenerate case: every certified trait carries a residual NaN. No crash -- the
    delegate is never called (nothing finite to compute), the run still persists with
    zero reported traits, and every trait lands in failed_traits."""
    reader = FakeReader()
    store = FakeResultStore()
    bad_df = pd.DataFrame(
        {
            "Barcode": [f"p{i}" for i in range(4)],
            "Genotype": ["A", "A", "B", "B"],
            "Replicate": [1, 2, 1, 2],
            "trait_a": [1.0, None, 3.0, 4.0],
        }
    )
    reader.add_cleaned_version("allnan.csv", "v1", bad_df, make_latest=True)
    _ports.configure(reader=reader, store=store)

    called = {"n": 0}
    real = descriptive_stats_tool.calculate_trait_statistics

    def _spy(*a, **k):
        called["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(descriptive_stats_tool, "calculate_trait_statistics", _spy)

    try:
        result = descriptive_stats(DescriptiveStatsParams(experiment="allnan.csv"))
        assert result.failed_traits == ["trait_a"]
        assert result.n_failed == 1
        assert result.n_traits_reported == 0
        assert result.stats_per_trait == []
        assert called["n"] == 0  # never delegated -- nothing finite to compute
        assert store.get_run("allnan.csv", "stats", "latest") is not None
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_delegate_missing_a_stat_key_is_surfaced_not_silently_none(
    injected_ports, monkeypatch
):
    """A delegate entry missing an expected stat key (e.g. a future upstream field
    rename/drop) must route to failed_traits -- never silently emit a row with that
    field defaulted to None, which would be indistinguishable from a genuine
    non-finite coercion (``r.get(k)`` on an absent key also returns None)."""
    real = descriptive_stats_tool.calculate_trait_statistics

    def _spy(df, trait_cols, **kwargs):
        out = real(df, trait_cols, **kwargs)
        del out["Holes"]["std"]
        return out

    monkeypatch.setattr(descriptive_stats_tool, "calculate_trait_statistics", _spy)

    result = _run()

    assert "Holes" not in {s.trait for s in result.stats_per_trait}
    assert "Holes" in result.failed_traits
    assert result.n_failed == 1
    assert "Holes" not in result.nonfinite_stat_traits


# ── 3.10 delegate-failure defense-in-depth ───────────────────────────────────


def test_delegate_reported_failed_trait_is_surfaced_not_raised(
    injected_ports, monkeypatch
):
    real = descriptive_stats_tool.calculate_trait_statistics

    def _spy(df, trait_cols, **kwargs):
        out = real(df, trait_cols, **kwargs)
        out["Holes"] = {"error": "No valid data"}
        return out

    monkeypatch.setattr(descriptive_stats_tool, "calculate_trait_statistics", _spy)

    result = _run()

    assert "Holes" not in {s.trait for s in result.stats_per_trait}
    assert result.n_failed == 1
    assert result.failed_traits == ["Holes"]


def test_delegate_omitting_a_trait_entirely_is_also_surfaced(
    injected_ports, monkeypatch
):
    """The delegate omitting a trait from its dict (not just an explicit "error" key)
    must route the same way -- never a KeyError/internal_error."""
    real = descriptive_stats_tool.calculate_trait_statistics

    def _spy(df, trait_cols, **kwargs):
        out = real(df, trait_cols, **kwargs)
        out.pop("Holes", None)
        return out

    monkeypatch.setattr(descriptive_stats_tool, "calculate_trait_statistics", _spy)

    result = _run()

    assert "Holes" not in {s.trait for s in result.stats_per_trait}
    assert "Holes" in result.failed_traits
    assert result.n_failed == 1


# ── 3.11 composition via the real ports over the in-memory object store ─────


def test_committed_stats_run_composes_over_real_ports(fake_supabase_storage):
    """After a descriptive_stats run commits via the real SupabaseResultStore (over
    the shared in-memory object store), its stats.csv is downloadable and a fresh
    SupabaseResultStore instance resolves the committed run by (experiment, "stats").

    NB: unlike ``qc``-class runs (qc_clean/remove_outliers), a ``stats``-class run is
    deliberately NOT part of ``_resolve_versioned_cleaned``'s cleaned-version lookup
    (see design.md Decision 2) -- this tool's output does not compose as another
    tool's input, so this test does not assert a ``require_clean`` read resolves it.
    """
    reader = FakeReader()
    reader.add_cleaned_version(_EXPERIMENT, "v1", _cleaned_df(), make_latest=True)
    store = SupabaseResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        result = _run()
        csv_key = result.outputs["stats.csv"]
        csv_bytes = fake_supabase_storage.objects[csv_key]
        parsed = pd.read_csv(pd.io.common.BytesIO(csv_bytes))
        fresh_store_run = SupabaseResultStore().get_run(_EXPERIMENT, "stats", "latest")
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert len(parsed) == result.n_traits_reported == 19
    assert fresh_store_run.run_ref == result.run_ref
    assert "stats.csv" in fresh_store_run.output_keys


# ── 3.12 second run increments version ───────────────────────────────────────


def test_second_run_increments_version(injected_ports):
    _reader, store = injected_ports
    _run()
    _run()
    assert [r.run_ref for r in store.list_runs(_EXPERIMENT, "stats")] == ["v1", "v2"]
    assert store.get_run(_EXPERIMENT, "stats", "latest").run_ref == "v2"


# ── 3.13 cylinder oracle (#483) — cylinder-scale (846-trait) correctness ─────
#
# turface_19's golden (19 traits) is fully recorded and fully checked above, but 19
# traits never exercises this tool's one genuinely scale-specific risk: cylinder's 846
# certified traits vastly exceed the 50-trait inline-summary cap, so a column-ordering
# bug, a cap-boundary bug, or a truncated persisted CSV silently losing rows could all
# hide behind a small fixture. See tests/fixtures/README.md's "Cross-tier oracle
# fixtures (cylinder)" section and cylinder_stats_golden.json's own _comment
# (independently computed via calculate_trait_statistics directly, regenerated by
# scripts/gen_stats_golden.py — not a live-stack call, unlike cylinder_qc_golden.json/
# cylinder_outlier_golden.json; cheap and fixture-based like turface_19's own stats
# golden). The live-stack cylinder *smoke* leg (real dev-stack container, truncation
# behavior only) is covered separately by test_descriptive_stats_smoke.py, marked
# live_smoke and excluded from per-PR CI per the /pre-merge convention.

_RAW_CYL = _FIXTURES / "cylinder_raw_data.csv"
_GOLDEN_CYL = json.loads(
    (_FIXTURES / "cylinder_stats_golden.json").read_text(encoding="utf-8")
)
_EXPERIMENT_CYL = "cylinder.csv"


def _cleaned_df_cyl() -> pd.DataFrame:
    raw = pd.read_csv(_RAW_CYL, encoding="utf-8")
    det = detect_columns(raw)
    cleaned, _kept, _log = clean_traits_for_analysis(
        raw, trait_cols=det["trait_cols"], **_roles(det)
    )
    return cleaned


def test_cylinder_scale_stats_match_golden_for_every_trait(fake_supabase_storage):
    """Cylinder counterpart to test_golden_stats_through_the_tool: every one of the
    846 certified traits' numeric values must match the independently-computed golden
    exactly — read from the REAL persisted stats.csv (SupabaseResultStore over the
    fake in-memory object store), since stats_per_trait itself is capped to 50 and
    cannot show all 846.
    """
    reader = FakeReader()
    reader.add_cleaned_version(
        _EXPERIMENT_CYL, "v1", _cleaned_df_cyl(), make_latest=True
    )
    store = SupabaseResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        result = descriptive_stats(DescriptiveStatsParams(experiment=_EXPERIMENT_CYL))
        csv_key = result.outputs["stats.csv"]
        csv_bytes = fake_supabase_storage.objects[csv_key]
        persisted = pd.read_csv(pd.io.common.BytesIO(csv_bytes)).set_index("trait")
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    # Structural correctness at scale: nothing silently dropped, cap applies to the
    # inline summary only, the persisted CSV is uncapped.
    assert result.n_samples == _GOLDEN_CYL["cleaned_samples"] == 129
    assert result.n_traits_requested == result.n_traits_reported == 846
    assert result.n_failed == 0
    assert result.truncated_in_summary is True
    assert len(result.stats_per_trait) == 50
    assert len(result.omitted_traits) == 846 - 50
    assert len(persisted) == 846

    # Numeric correctness for EVERY trait, not a sample of them.
    golden_traits = _GOLDEN_CYL["stats_per_trait"]
    assert set(persisted.index) == set(golden_traits)
    for trait, golden in golden_traits.items():
        row = persisted.loc[trait]
        assert int(row["n"]) == golden["count"], trait
        assert row["mean"] == pytest.approx(golden["mean"], abs=1e-9), trait
        assert row["std"] == pytest.approx(golden["std"], abs=1e-9), trait
        assert row["median"] == pytest.approx(golden["median"], abs=1e-9), trait
        assert row["q25"] == pytest.approx(golden["q25"], abs=1e-9), trait
        assert row["q75"] == pytest.approx(golden["q75"], abs=1e-9), trait
        assert row["min"] == pytest.approx(golden["min"], abs=1e-9), trait
        assert row["max"] == pytest.approx(golden["max"], abs=1e-9), trait
        assert row["cv"] == pytest.approx(golden["cv"], rel=1e-6, abs=1e-6), trait
        assert row["skewness"] == pytest.approx(golden["skewness"], abs=1e-6), trait
        assert row["kurtosis"] == pytest.approx(golden["kurtosis"], abs=1e-6), trait
