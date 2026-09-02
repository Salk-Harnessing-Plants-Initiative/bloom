"""Contract + golden tests for the granular ``heritability_analysis`` tool (#462).

The 9th granular consumer, and the first that *replaces* rather than adds: it retires
``plot_heritability_bar`` and ``plot_variance_decomposition`` into its ``include_plots`` /
``plots`` parameters. So this file has two jobs — pin the new tool's contract, and pin the
retirement (the absence assertions live in ``test_retirement.py``'s companion checks here
too, because a test that only asserts the new name would pass with both old tools still
registered).

The golden is a **characterization snapshot** of the delegate on turface_19, not ground
truth: a REML mixed-model fit has no closed form to hand-check against. See
``tests/fixtures/README.md``. Two consequences show up throughout:

* tolerance is ``rel=_H2_TOL`` (kept in sync with ``test_oracle.py``) **with an absolute
  floor**, because ``Lower.Root.Area.mm2`` lands at H2 ~ 7.67e-09 where no relative
  tolerance is meaningful;
* the discrete ``n_above_threshold`` count is the optimizer-robust guard, exactly as
  ``test_oracle.py`` uses it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import sys
from pathlib import Path

import pandas as pd
import pytest
from sleap_roots_analyze import clean_traits_for_analysis

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    FakeReader,
    SupabaseReader,
)
from bloom_mcp.experiment_utils import detect_columns
from bloom_mcp.result_store import FakeResultStore, RunStateError, SupabaseResultStore
from bloom_mcp.tools import _ports
from bloom_mcp.sections.sleap_roots.analysis import (
    heritability_analysis as heritability_module,
)
from bloom_mcp.sections.sleap_roots.analysis.heritability_analysis import (
    HeritabilityAnalysisParams,
    HeritabilityAnalysisResult,
    heritability_analysis,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_raw_data.csv"
_GOLDEN = json.loads(
    (_FIXTURES / "turface_19_heritability_golden.json").read_text(encoding="utf-8")
)

_EXPERIMENT = "turface_19.csv"

# Kept in sync with tests/test_oracle.py::_H2_TOL — the repo's existing tolerance for the
# same delegate on the same fixture. The absolute floor is not decoration: the golden's
# smallest h2 is ~7.7e-09 (see fixtures/README.md), where `rel` alone asserts nothing
# survivable across a BLAS/statsmodels change.
_H2_REL = 1e-5
_H2_ABS = 1e-6


def _roles(det: dict) -> dict[str, str]:
    roles = {
        "barcode_col": det["sample_id_col"],
        "genotype_col": det["genotype_col"],
        "replicate_col": det["replicate_col"],
    }
    return {k: v for k, v in roles.items() if v is not None}


def _cleaned_df() -> pd.DataFrame:
    """turface_19 cleaned at canonical defaults — the frame the golden was recorded on.

    Produced by the tested upstream ``clean_traits_for_analysis`` (not the code under
    test), exactly as ``qc_clean`` would.
    """
    raw = pd.read_csv(_RAW, encoding="utf-8")
    det = detect_columns(raw)
    cleaned, _kept, _log = clean_traits_for_analysis(
        raw, trait_cols=det["trait_cols"], **_roles(det)
    )
    return cleaned


def _synthetic_df(n_traits: int, *, n_genotypes: int = 5, n_reps: int = 4):
    """A certified-clean-shaped frame with ``n_traits`` non-constant numeric traits."""
    import numpy as np

    rng = np.random.default_rng(0)
    rows = n_genotypes * n_reps
    data = {
        "Barcode": [f"b{i}" for i in range(rows)],
        "geno": [f"g{i % n_genotypes}" for i in range(rows)],
        "rep": [i // n_genotypes for i in range(rows)],
    }
    for t in range(n_traits):
        base = np.repeat(rng.normal(size=n_genotypes), n_reps)
        data[f"trait_{t:03d}"] = base + rng.normal(scale=0.3, size=rows)
    return pd.DataFrame(data)


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


@pytest.fixture
def synthetic_ports():
    """Factory: seed a FakeReader with an arbitrary cleaned frame."""
    created: list = []

    def _make(df: pd.DataFrame, experiment: str = "synthetic.csv"):
        reader = FakeReader()
        store = FakeResultStore()
        reader.add_cleaned_version(experiment, "v1", df, make_latest=True)
        _ports.configure(reader=reader, store=store)
        created.append((reader, store))
        return reader, store

    try:
        yield _make
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


@pytest.fixture
def real_store_ports(fake_supabase_storage):
    """FakeReader + the REAL SupabaseResultStore over the in-memory object store.

    Required whenever a test reads a committed artifact's bytes: ``FakeResultStore``
    deletes its staging dir on commit and its ``output_keys`` are logical strings, not
    real paths — so an assertion against a staged file there silently no-ops (the same
    trap ``test_descriptive_stats_tool.py`` records having fallen into once).
    """
    created: dict = {}

    def _make(df: pd.DataFrame, experiment: str = _EXPERIMENT):
        reader = FakeReader()
        reader.add_cleaned_version(experiment, "v1", df, make_latest=True)
        store = SupabaseResultStore()
        _ports.configure(reader=reader, store=store)
        created["store"] = store
        return reader, store

    try:
        yield _make, fake_supabase_storage
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _artifact(storage, result: HeritabilityAnalysisResult, name: str) -> bytes:
    return storage.objects[result.outputs[name]]


def _table(storage, result: HeritabilityAnalysisResult) -> pd.DataFrame:
    return pd.read_csv(
        pd.io.common.BytesIO(_artifact(storage, result, "heritability.csv"))
    )


def _payload(storage, result: HeritabilityAnalysisResult) -> dict:
    return json.loads(_artifact(storage, result, "heritability_result.json"))


def _run(**overrides) -> HeritabilityAnalysisResult:
    params = {"experiment": _EXPERIMENT, **overrides}
    return heritability_analysis(HeritabilityAnalysisParams(**params))


def _trait(result: HeritabilityAnalysisResult, name: str):
    return next(t for t in result.per_trait if t.trait == name)


# ── 2. Golden through the tool (north star) ──────────────────────────────────


def test_golden_h2_through_the_tool(injected_ports):
    """2.2 — every recorded trait's H2 reproduces through the MCP boundary."""
    result = _run()

    assert set(_GOLDEN["per_trait"]) == {t.trait for t in result.per_trait}
    for trait, golden in _GOLDEN["per_trait"].items():
        got = _trait(result, trait)
        assert got.h2 == pytest.approx(
            golden["heritability"], rel=_H2_REL, abs=_H2_ABS
        ), trait

    assert result.method == _GOLDEN["method"] == "mixed_model"
    # Discrete count — the BLAS/optimizer-robust guard (see test_oracle.py).
    assert result.n_above_threshold == _GOLDEN["n_above_threshold"] == 8


def test_golden_variance_components_through_the_tool(injected_ports):
    """2.3 — the keys the variance-decomposition figure consumes come through
    non-defaulted. This is the happy-path half of bloommcp-packaging's "a renamed or
    dropped key SHALL fail rather than silently defaulting to zero"; the missing-key half
    is test_missing_variance_key_is_routed_not_zero_filled below."""
    result = _run()
    for trait, golden in _GOLDEN["per_trait"].items():
        got = _trait(result, trait)
        assert got.var_genetic == pytest.approx(golden["var_genetic"], rel=1e-5), trait
        assert got.var_residual == pytest.approx(
            golden["var_residual"], rel=1e-5
        ), trait
        assert got.n_genotypes == golden["n_genotypes"], trait
        assert got.n_observations == golden["n_observations"], trait
        assert got.model_type == golden["model_type"], trait


def test_counts_and_mean_are_consistent(injected_ports):
    """2.4 — nothing failed on the golden fixture, and the counts reconcile."""
    result = _run()
    assert result.n_traits_requested == result.n_traits_reported == 19
    assert result.n_failed == 0
    assert result.failed_traits == []
    assert result.nonfinite_traits == []
    assert result.mean_h2 == pytest.approx(_GOLDEN["mean_h2"], rel=_H2_REL, abs=_H2_ABS)
    assert result.n_samples == _GOLDEN["cleaned_samples"] == 158


def test_repeated_calls_are_deterministic(injected_ports):
    """2.5 — same process, same input, same delegate, no RNG: bit-identical.

    Deliberately exact rather than approx. This is an in-process claim only; the
    cross-environment claim is the golden's tolerance above.
    """
    first = {t.trait: t.h2 for t in _run().per_trait}
    second = {t.trait: t.h2 for t in _run().per_trait}
    assert first == second


# ── 3. Contract patterns ─────────────────────────────────────────────────────


def test_tool_appears_in_tools_list_with_migration_in_its_description():
    """3.1 — presence, schema, and the migration path a broken caller will look for.

    A caller who invokes a retired name gets "unknown tool" and re-reads tools/list; the
    description is the only surface that can tell them where the figure went.
    """
    from bloom_mcp import server
    from fastmcp import Client

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    tool = tools["sleap_roots_heritability_analysis"]
    assert tool.inputSchema is not None
    assert "plot_heritability_bar" in tool.description
    assert "plot_variance_decomposition" in tool.description
    assert "create_heritability_plot" in tool.description
    assert "create_variance_decomposition_plot" in tool.description
    # The one documented divergence between the numbers and the bar plot (design D1).
    assert "descending" in tool.description


def test_schema_round_trip_and_invalid_input(injected_ports):
    """3.2 — valid I/O validates; a missing required field and an out-of-range
    threshold are invalid_input."""
    result = _run()
    assert HeritabilityAnalysisResult.model_validate(
        json.loads(result.model_dump_json())
    )

    with pytest.raises(BloomMCPError) as exc:
        heritability_analysis(HeritabilityAnalysisParams.model_construct())
    assert exc.value.code in {"invalid_input", "internal_error"}

    for bad in (1.5, -0.1):
        with pytest.raises(Exception) as exc:
            _run(threshold=bad)
        assert "threshold" in str(exc.value)


def test_provenance_and_links(real_store_ports):
    """3.3 — provenance, based_on_version, both artifacts, and a parseable result JSON."""
    make, storage = real_store_ports
    _reader, store = make(_cleaned_df())
    result = _run()

    stored = store.get_run(_EXPERIMENT, "heritability", "latest")
    assert stored.tool == "heritability_analysis"
    assert stored.seed is None
    assert stored.based_on_version == "v1_cleaned" == result.source
    assert result.run_ref == stored.run_ref
    assert result.manifest_path == stored.manifest_path
    assert set(result.outputs) >= {"heritability.csv", "heritability_result.json"}
    assert set(result.output_links) == set(result.outputs)

    assert len(_table(storage, result)) == result.n_traits_reported == 19
    assert _payload(storage, result)["threshold"] == 0.5


def test_source_is_cleaned_and_payload_is_bounded(injected_ports):
    """3.3b — consumes the cleaned tier, and no inline field is an unbounded blob."""
    result = _run()
    assert result.source == "v1_cleaned"
    assert result.source != "raw"
    dumped = result.model_dump()
    assert not any(
        isinstance(v, (list, dict)) and len(str(v)) > 5000 for v in dumped.values()
    )


def test_persisted_json_is_uncapped_and_self_describing(real_store_ports):
    """3.3c — the persisted result JSON is not a copy of the truncated inline list.

    Also pins its top-level key set: mean_h2/n_above_threshold are @property on
    HeritabilityResult and are deliberately NOT serialized, so a reader of the JSON must
    not expect them.
    """
    make, storage = real_store_ports
    make(_synthetic_df(60), experiment="synthetic.csv")
    result = heritability_analysis(
        HeritabilityAnalysisParams(experiment="synthetic.csv", threshold=0.7)
    )
    payload = _payload(storage, result)
    assert set(payload) == {
        "method",
        "threshold",
        "per_trait",
        "failed_traits",
        "error",
    }
    assert payload["threshold"] == 0.7
    assert len(payload["per_trait"]) == result.n_traits_reported == 60
    assert len(result.per_trait) == 50  # inline list is capped; the JSON is not
    for entry in payload["per_trait"]:
        assert set(entry) == {
            "trait",
            "h2",
            "passed_threshold",
            "var_genetic",
            "var_residual",
            "n_genotypes",
            "n_observations",
            "model_type",
        }


def test_delegate_called_exactly_once_even_with_both_plots(injected_ports, monkeypatch):
    """3.4 — the plot/number consistency guarantee's structural half."""
    real = heritability_module.calculate_heritability_estimates
    calls: list = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(heritability_module, "calculate_heritability_estimates", _spy)
    _run(include_plots=True)
    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert kwargs["genotype_col"] == "geno"
    assert kwargs["replicate_col"] == "rep"


def test_module_contains_no_heritability_math():
    """3.4 — the tool delegates; it must not re-implement the mixed model."""
    src = Path(heritability_module.__file__).read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]  # skip the module docstring's prose
    for forbidden in ("mixedlm", "cov_re", ".scale", "MixedLM"):
        assert forbidden not in body, forbidden


def test_uncleaned_input_is_tool_error_with_a_remedy(injected_ports):
    """3.5 — matches pca_analysis/clustering's code for this guard, not remove_outliers'."""
    reader, store = injected_ports

    def _raise(*_a, **_k):
        raise CleanedVersionRequiredError("no cleaned version")

    reader.load_experiment = _raise  # type: ignore[method-assign]
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "qc_clean" in exc.value.remedy
    assert store.list_runs(_EXPERIMENT, "heritability") == []


def test_version_selector_is_passed_through(injected_ports, monkeypatch):
    """3.5b — omitting `version` keeps today's call shape; supplying it pins."""
    reader, _store = injected_ports
    seen: list = []
    real = reader.load_experiment

    def _spy(name, **kwargs):
        seen.append(kwargs)
        return real(name, **kwargs)

    monkeypatch.setattr(reader, "load_experiment", _spy)
    _run()
    assert "version" not in seen[-1]
    _run(version="v1")
    assert seen[-1]["version"] == "v1"


@pytest.mark.parametrize(
    "bad",
    [
        ["not_a_column"],
        ["Barcode"],
        [],
        ["Shoot_Biomass_mg", "Shoot_Biomass_mg"],
    ],
    ids=["unknown", "non-certified-identifier", "empty", "duplicate"],
)
def test_trait_selection_is_validated(injected_ports, bad):
    """3.6 — reuses _validate_trait_subset(require_certified=True) unchanged."""
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=bad)
    assert exc.value.code == "invalid_input"
    assert store.list_runs(_EXPERIMENT, "heritability") == []


def test_validate_trait_subset_is_the_shared_helper():
    """3.6 — the same helper pca_analysis/clustering import, not a reimplementation."""
    from bloom_mcp.sections.sleap_roots.analysis import pca_analysis as pca_module

    assert (
        heritability_module._validate_trait_subset is pca_module._validate_trait_subset
    )


def test_explicit_subset_narrows_both_the_result_and_the_delegate_call(
    injected_ports, monkeypatch
):
    """3.6 — an explicit selection reaches the delegate, not just the output filter."""
    real = heritability_module.calculate_heritability_estimates
    seen: list = []

    def _spy(df, trait_cols, **kwargs):
        seen.append(list(trait_cols))
        return real(df, trait_cols, **kwargs)

    monkeypatch.setattr(heritability_module, "calculate_heritability_estimates", _spy)
    picked = ["Shoot_Biomass_mg", "Solidity"]
    result = _run(trait_columns=picked)
    assert seen == [picked]
    assert [t.trait for t in result.per_trait] == picked


# ── 3.7 genotype/replicate roles (design D3) ─────────────────────────────────


def test_missing_replicate_column_still_analyzes(synthetic_ports):
    """3.7a — the deliberate loosening. SupabaseReader resolves replicate_col=None for
    every frame it produces, so this is the ONLY path a DB-backed experiment has."""
    df = _cleaned_df().drop(columns=["rep"])
    _reader, store = synthetic_ports(df, experiment="no_rep.csv")
    result = heritability_analysis(HeritabilityAnalysisParams(experiment="no_rep.csv"))
    assert result.replicate_col is None
    assert result.n_traits_reported == 19
    assert store.list_runs("no_rep.csv", "heritability")


def test_replicate_column_does_not_change_the_estimate(injected_ports, synthetic_ports):
    """3.7c — pins upstream's "replicate values never enter the model" claim rather than
    trusting its docstring: the model is value ~ 1 + (1|genotype)."""
    with_rep = {t.trait: t.h2 for t in _run().per_trait}
    synthetic_ports(_cleaned_df().drop(columns=["rep"]), experiment="no_rep.csv")
    without = {
        t.trait: t.h2
        for t in heritability_analysis(
            HeritabilityAnalysisParams(experiment="no_rep.csv")
        ).per_trait
    }
    assert with_rep == without


def test_missing_genotype_column_is_assumption_violated(synthetic_ports):
    """3.7b — nothing in the request is wrong, so it is not invalid_input."""
    _reader, store = synthetic_ports(
        _cleaned_df().drop(columns=["geno"]), experiment="no_geno.csv"
    )
    with pytest.raises(BloomMCPError) as exc:
        heritability_analysis(HeritabilityAnalysisParams(experiment="no_geno.csv"))
    assert exc.value.code == "assumption_violated"
    assert "genotype" in exc.value.message
    assert store.list_runs("no_geno.csv", "heritability") == []


# ── 3.8 error envelope ───────────────────────────────────────────────────────


def test_run_level_delegate_error_is_structured_and_does_not_leak(
    injected_ports, monkeypatch
):
    """3.8 — a run-level short-circuit is an error, not an empty run, and the delegate's
    own message is not echoed verbatim."""
    _reader, store = injected_ports
    monkeypatch.setattr(
        heritability_module,
        "calculate_heritability_estimates",
        lambda *a, **k: {"error": "Missing required columns: ['/var/secrets/key']"},
    )
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "assumption_violated"
    assert "/var/secrets/key" not in exc.value.message
    assert store.list_runs(_EXPERIMENT, "heritability") == []


def test_commit_failure_surfaces_as_tool_error(injected_ports):
    """3.8b(a)"""
    _reader, store = injected_ports
    store.fail_next_commit(_EXPERIMENT, "heritability")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "commit failed for heritability" in exc.value.message


def test_manifest_read_failure_surfaces_as_tool_error(injected_ports):
    """3.8b(b)"""
    _reader, store = injected_ports
    store.fail_next_read(_EXPERIMENT, "heritability")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "manifest read failure" in exc.value.message


def test_run_state_error_from_commit_still_maps_to_internal_error(
    injected_ports, monkeypatch
):
    """3.8b(c) — proves errors= wasn't widened to the ResultStoreError base."""
    _reader, store = injected_ports

    def _boom(run, outputs):
        raise RunStateError("commit() on an unknown or already-committed run")

    monkeypatch.setattr(store, "commit", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "internal_error"


@pytest.mark.parametrize(
    "target",
    [
        "calculate_heritability_estimates",
        "create_heritability_plot",
        "create_variance_decomposition_plot",
        "compare_trait_heritabilities",
    ],
)
def test_a_raising_delegate_leaks_nothing_into_the_envelope(
    injected_ports, monkeypatch, target
):
    """3.8b(d) — no backend internals in the user-facing message/remedy, no run left."""
    _reader, store = injected_ports
    secret = "secret path /var/secrets/key and host db.internal"

    def _boom(*_a, **_k):
        raise RuntimeError(secret)

    if target == "calculate_heritability_estimates":
        monkeypatch.setattr(heritability_module, target, _boom)
        kwargs = {}
    else:
        import sleap_roots_analyze.statistics as stats_mod
        import sleap_roots_analyze.visualization as viz_mod

        monkeypatch.setattr(viz_mod, target, _boom, raising=False)
        monkeypatch.setattr(stats_mod, target, _boom, raising=False)
        kwargs = {"include_plots": True}

    with pytest.raises(BloomMCPError) as exc:
        _run(**kwargs)
    blob = f"{exc.value.message} {exc.value.remedy}"
    assert "/var" not in blob
    assert "db.internal" not in blob
    assert store.list_runs(_EXPERIMENT, "heritability") == []


# ── 3.9 / 3.10 failure + scrub routing ───────────────────────────────────────


def _patched_delegate(monkeypatch, mutate):
    real = heritability_module.calculate_heritability_estimates

    def _wrapped(*args, **kwargs):
        out = dict(real(*args, **kwargs))
        mutate(out)
        return out

    monkeypatch.setattr(
        heritability_module, "calculate_heritability_estimates", _wrapped
    )


def test_delegate_reported_trait_failure_does_not_fail_the_run(
    injected_ports, monkeypatch
):
    """3.9a"""
    _reader, store = injected_ports
    _patched_delegate(
        monkeypatch, lambda d: d.__setitem__("Solidity", {"error": "no fit"})
    )
    result = _run()
    assert result.failed_traits == ["Solidity"]
    assert result.n_failed == 1
    assert result.n_traits_reported == 18
    assert store.list_runs(_EXPERIMENT, "heritability")


def test_delegate_omitting_a_trait_entirely_is_also_surfaced(
    injected_ports, monkeypatch
):
    """3.9b — the case HeritabilityResult.from_heritability_dict cannot see: it iterates
    only the keys the delegate returned, so an omitted trait yields failed_traits == [].
    The tool reconciles against the requested list instead."""
    _reader, store = injected_ports
    _patched_delegate(monkeypatch, lambda d: d.pop("Solidity"))
    result = _run()
    assert result.failed_traits == ["Solidity"]
    assert result.n_failed == 1
    assert result.n_traits_requested == result.n_traits_reported + result.n_failed


def test_all_traits_failing_persists_a_run_and_reports_no_mean(
    real_store_ports, monkeypatch
):
    """3.9c — mean_h2 must be None, not 0.0. HeritabilityResult.mean_h2 returns 0.0 for
    an empty per_trait, which an agent would read as "heritability is zero"."""
    make, storage = real_store_ports
    _reader, store = make(_cleaned_df())
    _patched_delegate(
        monkeypatch,
        lambda d: d.update(
            {k: {"error": "no fit"} for k in d if k != "__calculation_metadata__"}
        ),
    )
    result = _run()
    assert result.n_traits_reported == 0
    assert result.per_trait == []
    assert result.mean_h2 is None
    assert len(result.failed_traits) == 19
    table = _table(storage, result)
    assert table.empty
    # Header present even with zero rows — a downstream reader must not have to
    # special-case "no columns to parse from file" for a legitimate all-failed run.
    assert list(table.columns) == [
        "trait",
        "h2",
        "passed_threshold",
        "var_genetic",
        "var_residual",
        "n_genotypes",
        "n_observations",
        "model_type",
    ]
    _payload(storage, result)  # parses -> to_json's allow_nan=False held


@pytest.mark.parametrize("key", ["heritability", "var_genetic", "var_residual"])
def test_nonfinite_value_is_routed_not_emitted(real_store_ports, monkeypatch, key):
    """3.10 — routed to failed_traits AND named in nonfinite_traits, and the run still
    persists (to_json's allow_nan=False would otherwise have failed the whole run).

    Reachable only by monkeypatch: the delegate clamps via max(0, min(1, h2)), and
    max(0, min(1, nan)) evaluates to 1 in Python — every comparison with nan is False, so
    min returns its first argument. The scrub's real day job is the missing-key case
    below; this branch is defense-in-depth against a delegate change.
    """
    make, storage = real_store_ports
    make(_cleaned_df())
    _patched_delegate(
        monkeypatch, lambda d: d["Solidity"].__setitem__(key, float("nan"))
    )
    result = _run()
    assert result.nonfinite_traits == ["Solidity"]
    assert "Solidity" in result.failed_traits
    assert all(t.trait != "Solidity" for t in result.per_trait)
    assert result.mean_h2 is not None and math.isfinite(result.mean_h2)
    assert "Solidity" not in set(_table(storage, result)["trait"])
    csv_text = _artifact(storage, result, "heritability.csv").decode("utf-8").lower()
    assert "nan" not in csv_text and "inf" not in csv_text
    _payload(storage, result)


@pytest.mark.parametrize("key", ["var_genetic", "var_residual", "n_genotypes"])
def test_missing_variance_key_is_routed_not_zero_filled(
    real_store_ports, monkeypatch, key
):
    """3.10b — the guard bloommcp-packaging requires, on the DEFAULT (no-plot) path.

    HeritabilityResult.from_heritability_dict does float(entry.get("var_genetic", 0.0)),
    so a renamed upstream key would otherwise be emitted as a plausible zero variance
    component — and the plot-path variance guard never runs here to catch it. Presence,
    not just finiteness, has to be checked.
    """
    make, storage = real_store_ports
    make(_cleaned_df())
    _patched_delegate(monkeypatch, lambda d: d["Solidity"].pop(key))
    result = _run()
    assert "Solidity" in result.failed_traits
    # Not a numeric edge case — a contract breakage. Not reported as "non-finite".
    assert result.nonfinite_traits == []
    assert all(t.trait != "Solidity" for t in result.per_trait)
    assert "Solidity" not in set(_table(storage, result)["trait"])
    payload = _payload(storage, result)
    assert all(e["trait"] != "Solidity" for e in payload["per_trait"])


def test_the_delegates_own_return_is_not_mutated(injected_ports, monkeypatch):
    """3.10 — from_heritability_dict promises not to mutate; so does the scrub."""
    captured: dict = {}
    real = heritability_module.calculate_heritability_estimates

    def _wrapped(*args, **kwargs):
        out = real(*args, **kwargs)
        out["Solidity"]["heritability"] = float("nan")
        captured["dict"] = out
        captured["before"] = dict(out["Solidity"])
        return out

    monkeypatch.setattr(
        heritability_module, "calculate_heritability_estimates", _wrapped
    )
    _run()
    assert captured["dict"]["Solidity"] == captured["before"]


# ── 3.11 the 50-trait cap ────────────────────────────────────────────────────


def test_wide_experiment_truncates_inline_but_not_the_persisted_table(real_store_ports):
    """3.11"""
    make, storage = real_store_ports
    make(_synthetic_df(60), experiment="synthetic.csv")
    result = heritability_analysis(
        HeritabilityAnalysisParams(experiment="synthetic.csv")
    )
    assert len(result.per_trait) == 50
    assert result.truncated_in_summary is True
    assert result.omitted_traits == [f"trait_{i:03d}" for i in range(50, 60)]
    assert len(_table(storage, result)) == 60


def test_narrow_experiment_is_not_truncated(injected_ports):
    """3.11 — the other side of the cap."""
    result = _run()
    assert result.truncated_in_summary is False
    assert result.omitted_traits == []
    assert len(result.per_trait) == 19


# ── 3.12 composition + versioning ────────────────────────────────────────────


def test_committed_run_composes_over_real_ports(fake_supabase_storage):
    """3.12"""
    reader = FakeReader()
    reader.add_cleaned_version(_EXPERIMENT, "v1", _cleaned_df(), make_latest=True)
    _ports.configure(reader=reader, store=SupabaseResultStore())
    try:
        result = _run()
        csv_bytes = fake_supabase_storage.objects[result.outputs["heritability.csv"]]
        parsed = pd.read_csv(pd.io.common.BytesIO(csv_bytes))
        fresh = SupabaseResultStore().get_run(_EXPERIMENT, "heritability", "latest")
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert len(parsed) == result.n_traits_reported == 19
    assert fresh.run_ref == result.run_ref
    assert "heritability_result.json" in fresh.output_keys


def test_second_run_increments_version(injected_ports):
    """3.12"""
    _reader, store = injected_ports
    _run()
    _run()
    assert [r.run_ref for r in store.list_runs(_EXPERIMENT, "heritability")] == [
        "v1",
        "v2",
    ]


# ── 3.13 discovery ──────────────────────────────────────────────────────────


def test_heritability_is_a_discoverable_tool_class():
    """3.13 — without this, runs persist correctly but list_existing_analyses never
    asks for them (the bloom#669 gap, for a class that had never been written to)."""
    from bloom_mcp.manifest import CANONICAL_TOOL_CLASSES
    from bloom_mcp.sections.core.list_existing_analyses import (
        TOOL_CLASSES,
        _TOOL_CLASS_TO_PUBLIC_NAME,
    )

    assert "heritability" in TOOL_CLASSES
    assert "heritability" in CANONICAL_TOOL_CLASSES
    assert _TOOL_CLASS_TO_PUBLIC_NAME["heritability"] == "heritability_analysis"


# ── 3.15 the retained invariant ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "subset",
    [
        None,
        ["Shoot_Biomass_mg"],
        ["Shoot_Biomass_mg", "Solidity", "Perimeter.mm"],
    ],
)
def test_trait_counts_reconcile_for_any_valid_subset(injected_ports, subset):
    """3.15 — the invariant kept in place of a full property-based suite (see the
    proposal's out-of-scope note): requested == reported + failed, always."""
    result = _run(**({} if subset is None else {"trait_columns": subset}))
    assert result.n_traits_requested == result.n_traits_reported + result.n_failed
    assert set(result.nonfinite_traits) <= set(result.failed_traits)


# ── 4. the folded-in plots ───────────────────────────────────────────────────


def test_default_path_generates_no_figures(injected_ports, monkeypatch):
    """4.1 — and compare_trait_heritabilities is never called."""
    import sleap_roots_analyze.statistics as stats_mod

    called: list = []
    monkeypatch.setattr(
        stats_mod,
        "compare_trait_heritabilities",
        lambda *a, **k: called.append(1),
    )
    result = _run()
    assert set(result.outputs) == {"heritability.csv", "heritability_result.json"}
    assert called == []


def test_default_path_does_not_import_matplotlib(injected_ports, monkeypatch):
    """4.1b — the Tier-0 import-clean guarantee the spec requires."""
    monkeypatch.setitem(sys.modules, "matplotlib", None)
    result = _run()
    assert result.n_traits_reported == 19


def test_plots_value_with_include_plots_false_is_ignored(injected_ports):
    """4.2"""
    result = _run(plots=["create_heritability_plot"])
    assert set(result.outputs) == {"heritability.csv", "heritability_result.json"}


@pytest.mark.parametrize(
    "plots",
    [[], ["nope"], ["create_heritability_plot", "create_heritability_plot"]],
    ids=["empty", "unknown", "duplicate"],
)
def test_invalid_plot_keys_reject_before_any_commit(injected_ports, plots):
    """4.3"""
    import matplotlib.pyplot as plt

    _reader, store = injected_ports
    plt.close("all")
    with pytest.raises(BloomMCPError) as exc:
        _run(include_plots=True, plots=plots)
    assert exc.value.code == "invalid_input"
    assert store.list_runs(_EXPERIMENT, "heritability") == []
    assert plt.get_fignums() == []


def test_plotters_receive_the_same_values_the_result_reports(
    injected_ports, monkeypatch
):
    """4.4 — the headline oracle. Both figures and the numbers come from one dict."""
    import sleap_roots_analyze.visualization as viz_mod

    seen: dict = {}
    real_bar = viz_mod.create_heritability_plot

    def _spy_bar(results, **kwargs):
        seen["bar"] = results
        return real_bar(results, **kwargs)

    monkeypatch.setattr(viz_mod, "create_heritability_plot", _spy_bar)
    result = _run(include_plots=True, plots=["create_heritability_plot"])

    plotted = seen["bar"]
    for row in result.per_trait:
        assert plotted[row.trait]["heritability"] == pytest.approx(row.h2, rel=0, abs=0)


def test_inline_order_is_trait_order_while_the_bar_plot_sorts_by_h2(synthetic_ports):
    """4.4b — the one documented divergence (design D1). Same numbers, different slice:
    on a wide experiment the inline top-50 is NOT the first plotted page."""
    _reader, _store = synthetic_ports(_synthetic_df(60))
    result = heritability_analysis(
        HeritabilityAnalysisParams(experiment="synthetic.csv")
    )
    inline = [t.trait for t in result.per_trait]
    assert inline == sorted(inline)  # resolved trait order, not H2 order
    by_h2 = [t.trait for t in sorted(result.per_trait, key=lambda t: -t.h2)]
    assert inline != by_h2
    assert "descending" in (heritability_analysis.__doc__ or "")


def test_threshold_reaches_every_consumer(injected_ports, monkeypatch):
    """4.5 — including create_variance_decomposition_plot, whose own default is 0.3."""
    import sleap_roots_analyze.visualization as viz_mod

    seen: dict = {}
    real_bar = viz_mod.create_heritability_plot
    real_vd = viz_mod.create_variance_decomposition_plot

    def _spy_bar(results, **kwargs):
        seen["bar"] = kwargs.get("threshold")
        return real_bar(results, **kwargs)

    def _spy_vd(comparison, **kwargs):
        seen["vd"] = kwargs.get("threshold")
        return real_vd(comparison, **kwargs)

    monkeypatch.setattr(viz_mod, "create_heritability_plot", _spy_bar)
    monkeypatch.setattr(viz_mod, "create_variance_decomposition_plot", _spy_vd)

    result = _run(include_plots=True, threshold=0.7)
    assert seen["bar"] == 0.7
    assert seen["vd"] == 0.7
    assert result.threshold == 0.7
    assert result.n_above_threshold == sum(1 for t in result.per_trait if t.h2 >= 0.7)
    for row in result.per_trait:
        assert row.passed_threshold == (row.h2 >= 0.7)


def test_comparison_table_is_lazy_and_delegated(injected_ports, monkeypatch):
    """4.6 — computed only for its own figure, and the plotter gets its return."""
    import sleap_roots_analyze.statistics as stats_mod
    import sleap_roots_analyze.visualization as viz_mod

    calls: list = []
    real_cmp = stats_mod.compare_trait_heritabilities

    def _spy_cmp(*args, **kwargs):
        out = real_cmp(*args, **kwargs)
        calls.append(out)
        return out

    monkeypatch.setattr(stats_mod, "compare_trait_heritabilities", _spy_cmp)

    _run(include_plots=True, plots=["create_heritability_plot"])
    assert calls == []

    seen: dict = {}
    real_vd = viz_mod.create_variance_decomposition_plot

    def _spy_vd(comparison, **kwargs):
        seen["frame"] = comparison
        return real_vd(comparison, **kwargs)

    monkeypatch.setattr(viz_mod, "create_variance_decomposition_plot", _spy_vd)
    _run(include_plots=True, plots=["create_variance_decomposition_plot"])
    assert len(calls) == 1
    # The plotter's frame is the helper's return (after the documented NaN-row drop),
    # not a re-derived table.
    assert set(seen["frame"]["trait"]) <= set(calls[0]["trait"])


def test_scored_trait_missing_a_variance_component_refuses_to_plot(
    injected_ports, monkeypatch
):
    """4.7 — refuse a zero-filled decomposition; no run committed."""
    import sleap_roots_analyze.statistics as stats_mod

    real_cmp = stats_mod.compare_trait_heritabilities

    def _break(*args, **kwargs):
        out = real_cmp(*args, **kwargs).copy()
        out.loc[out.index[0], "var_genetic"] = float("nan")
        return out

    monkeypatch.setattr(stats_mod, "compare_trait_heritabilities", _break)
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        _run(include_plots=True, plots=["create_variance_decomposition_plot"])
    assert exc.value.code == "assumption_violated"
    assert store.list_runs(_EXPERIMENT, "heritability") == []


def test_unscored_trait_is_dropped_not_escalated(injected_ports, monkeypatch):
    """4.7 — a NaN-heritability row is a drop, not the contract-breakage error."""
    _patched_delegate(
        monkeypatch, lambda d: d.__setitem__("Solidity", {"error": "no fit"})
    )
    result = _run(include_plots=True, plots=["create_variance_decomposition_plot"])
    assert "create_variance_decomposition_plot.png" in result.outputs


def test_empty_comparison_frame_skips_the_figure(injected_ports, monkeypatch):
    """4.7b — an empty decomposition figure is not a useful artifact; failed_traits
    already names why it is missing."""
    _patched_delegate(
        monkeypatch,
        lambda d: d.update(
            {k: {"error": "no fit"} for k in d if k != "__calculation_metadata__"}
        ),
    )
    result = _run(include_plots=True, plots=["create_variance_decomposition_plot"])
    assert "create_variance_decomposition_plot.png" not in result.outputs
    assert len(result.failed_traits) == 19


def test_figures_are_persisted_as_valid_pngs_and_closed(real_store_ports):
    """4.8 — both figures land in the same run as real PNGs, and nothing stays open."""
    import matplotlib.pyplot as plt

    make, storage = real_store_ports
    make(_cleaned_df())
    plt.close("all")
    result = _run(include_plots=True)
    assert set(result.outputs) == {
        "heritability.csv",
        "heritability_result.json",
        "create_heritability_plot.png",
        "create_variance_decomposition_plot.png",
    }
    for name in (
        "create_heritability_plot.png",
        "create_variance_decomposition_plot.png",
    ):
        assert _artifact(storage, result, name)[:4] == b"\x89PNG"
    assert plt.get_fignums() == []


@pytest.mark.parametrize("failure", ["plotter", "commit"])
def test_figures_close_on_every_failure_path(injected_ports, monkeypatch, failure):
    """4.8 — cleanup on a mid-generation plotter failure and on a failing commit."""
    import matplotlib.pyplot as plt
    import sleap_roots_analyze.visualization as viz_mod

    _reader, store = injected_ports
    plt.close("all")

    if failure == "plotter":
        real_vd = viz_mod.create_variance_decomposition_plot

        def _boom(*_a, **_k):
            raise RuntimeError("second plotter blew up")

        monkeypatch.setattr(viz_mod, "create_variance_decomposition_plot", _boom)
        assert real_vd is not None
    else:
        store.fail_next_commit(_EXPERIMENT, "heritability")

    with pytest.raises(Exception):
        _run(include_plots=True)
    assert plt.get_fignums() == []


def test_paginated_bar_plot_persists_one_png_per_page(synthetic_ports):
    """4.9 — create_heritability_plot returns a list above its traits_per_page default
    (50); each page must persist and close, not leak as a list under one key."""
    import matplotlib.pyplot as plt

    _reader, _store = synthetic_ports(_synthetic_df(60))
    plt.close("all")
    result = heritability_analysis(
        HeritabilityAnalysisParams(
            experiment="synthetic.csv",
            include_plots=True,
            plots=["create_heritability_plot"],
        )
    )
    pages = sorted(
        k for k in result.outputs if k.startswith("create_heritability_plot")
    )
    assert pages == [
        "create_heritability_plot_page1.png",
        "create_heritability_plot_page2.png",
    ]
    assert plt.get_fignums() == []
