"""Contract + oracle tests for the contract-wrapped ``plot_trait_boxplots`` tool (#466).

Converges the tool onto ``@as_mcp_tool`` — Pydantic I/O, structured ``BloomMCPError``, one
stamped ``Provenance``, versioned ``ResultStore`` persistence under its own tool class — mirroring
``qc_inspect``'s read-only, pre-clean EDA pattern. A batched render (above
``_viz_shared.TRAIT_BATCH_THRESHOLD`` traits) persists one committed output per page. Requires an
auto-detected genotype column (no override parameter).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import (
    FakeResultStore,
    SupabaseResultStore,
)
from bloom_mcp.sections.sleap_roots.analysis import _viz_shared
from bloom_mcp.tools import _ports
from bloom_mcp.sections.sleap_roots.analysis import (
    plot_trait_boxplots as plot_trait_boxplots_tool,
)
from bloom_mcp.sections.sleap_roots.analysis.plot_trait_boxplots import (
    PlotTraitBoxplotsParams,
    PlotTraitBoxplotsResult,
    plot_trait_boxplots,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_raw_data.csv"
_EXPERIMENT = "turface_19_raw.csv"

_DELEGATE_BATCH_SIZE = 16


def _raw_df() -> pd.DataFrame:
    return pd.read_csv(_RAW, encoding="utf-8")


def _wide_df(n_traits: int) -> pd.DataFrame:
    n_samples = 12
    data = {"geno": [f"G{i % 3}" for i in range(n_samples)]}
    for t in range(n_traits):
        data[f"trait_{t}"] = [float(i + t) for i in range(n_samples)]
    return pd.DataFrame(data)


def _expected_pages(n_traits: int) -> int:
    return -(-n_traits // _DELEGATE_BATCH_SIZE)


@pytest.fixture
def injected_ports():
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_experiment(_EXPERIMENT, _raw_df())
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _run(**overrides) -> PlotTraitBoxplotsResult:
    return plot_trait_boxplots(
        PlotTraitBoxplotsParams(experiment=_EXPERIMENT, **overrides)
    )


# ── genotype-required guard ──────────────────────────────────────────────────


def test_no_detectable_genotype_column_is_assumption_violated(monkeypatch):
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(8)],
            "t1": [float(i) for i in range(8)],
            "t2": [float(2 * i) for i in range(8)],
        }
    )
    reader = FakeReader()
    reader.add_experiment("no_geno.csv", df)
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    calls = {"n": 0}
    real = plot_trait_boxplots_tool.create_trait_boxplots_by_genotype

    def _spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(
        plot_trait_boxplots_tool, "create_trait_boxplots_by_genotype", _spy
    )
    try:
        with pytest.raises(BloomMCPError) as exc:
            plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="no_geno.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert exc.value.code == "assumption_violated"
    assert "no_geno.csv" in exc.value.message
    assert calls["n"] == 0
    assert store.list_runs("no_geno.csv", "trait_boxplots") == []


# ── delegation pinning + batching boundary ──────────────────────────────────


def test_delegates_unbatched_below_threshold(injected_ports, monkeypatch):
    calls = {"n": 0}
    real = plot_trait_boxplots_tool.create_trait_boxplots_by_genotype

    def _spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(
        plot_trait_boxplots_tool, "create_trait_boxplots_by_genotype", _spy
    )
    result = _run()
    assert calls["n"] == 1
    assert result.batched is False
    assert result.n_pages == 1
    assert result.genotype_column == "geno"


def test_batches_above_threshold(monkeypatch):
    wide_experiment = "wide.csv"
    n_traits = 60
    reader = FakeReader()
    reader.add_experiment(wide_experiment, _wide_df(n_traits))
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        calls = {"n": 0}
        real = plot_trait_boxplots_tool.create_trait_boxplots_by_genotype_batched

        def _spy(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(
            plot_trait_boxplots_tool, "create_trait_boxplots_by_genotype_batched", _spy
        )
        result = plot_trait_boxplots(
            PlotTraitBoxplotsParams(experiment=wide_experiment)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert calls["n"] == 1
    assert result.batched is True
    expected = _expected_pages(n_traits)
    assert result.n_pages == expected
    # One figure per page PLUS the committed sample-size table (#748): the table is an
    # output, not a page, so n_pages and page_traits still count only rendered figures.
    assert len(result.outputs) == expected + 1
    assert len(result.page_traits) == expected


def test_batched_commit_failing_partway_through_persists_nothing(monkeypatch):
    """Tool-layer analog of test_store_parity.py's generic partial-commit coverage — see
    the identical test in test_plot_trait_histograms_tool.py for the full rationale
    (#466 review)."""
    wide_experiment = "wide.csv"
    n_traits = 60
    reader = FakeReader()
    reader.add_experiment(wide_experiment, _wide_df(n_traits))
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        expected_pages = _expected_pages(n_traits)
        assert 0 < 2 < expected_pages
        store.fail_next_commit(wide_experiment, "trait_boxplots", after_outputs=2)

        captured = {}
        real_create = store.create_run

        def _spy_create(*a, **k):
            run = real_create(*a, **k)
            captured["staging_dir"] = run.staging_dir
            return run

        monkeypatch.setattr(store, "create_run", _spy_create)

        with pytest.raises(BloomMCPError) as exc:
            plot_trait_boxplots(PlotTraitBoxplotsParams(experiment=wide_experiment))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert exc.value.code == "tool_error"
    assert store.list_runs(wide_experiment, "trait_boxplots") == []
    assert not captured["staging_dir"].exists()


@pytest.mark.parametrize(
    "n_traits, expect_batched",
    [(50, False), (51, True)],
)
def test_batching_boundary_matches_threshold(n_traits, expect_batched):
    assert _viz_shared.TRAIT_BATCH_THRESHOLD == 50
    experiment = "boundary.csv"
    reader = FakeReader()
    reader.add_experiment(experiment, _wide_df(n_traits))
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment=experiment))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert result.batched is expect_batched


def test_delegate_batch_size_matches_live_default():
    """Pins _DELEGATE_BATCH_SIZE against the live delegate signature so a future
    sleap-roots-analyze bump that changes its default is caught here, not silently
    desyncing page_traits' chunking from what actually landed on each rendered page."""
    import inspect

    default = (
        inspect.signature(
            plot_trait_boxplots_tool.create_trait_boxplots_by_genotype_batched
        )
        .parameters["batch_size"]
        .default
    )
    assert default == plot_trait_boxplots_tool._DELEGATE_BATCH_SIZE


def _titled_traits(fig) -> list[str]:
    """Extract the trait name each subplot's title actually names — create_trait_boxplots_
    by_genotype_batched titles each axis with the bare trait name (verified against the
    live delegate). Filters to visible axes only: this delegate DOES pad a not-exactly-full
    page with extra, invisible, blank-titled axes, same as create_trait_histograms_batched —
    corrected in #466 review round 6 after a prior version of this comment wrongly claimed
    it doesn't. Its grid-sizing is adaptive (an n_traits=65 page happened to land on an
    exact 1x1 fit with no padding at all, which is what led to that wrong claim), so a
    remainder that doesn't fit its chosen grid exactly (confirmed directly against the live
    delegate for remainders of 5 and 8 out of a 16-per-page-equivalent count, not assumed
    from the one n_traits=65 case) pads just like the fixed-4-column histograms delegate.
    """
    return [ax.get_title() for ax in fig.axes if ax.get_visible()]


@pytest.mark.parametrize(
    "n_traits", [60, 64, 65, 69]
)  # 64: an exact multiple of batch_size (16); 65: one leftover trait, which this
# delegate's adaptive grid happens to fit exactly (no padding); 69: 5 leftover traits,
# which it does NOT fit exactly — genuinely exercises the invisible-blank-axes filter
# for this tool (#466 review round 6 — a prior version of this suite wrongly assumed
# this delegate never pads based only on the n_traits=65 case, which is misleading:
# it's this delegate's *adaptive* grid sizing landing on an exact fit at that specific
# remainder, not an absence of padding in general).
def test_page_traits_maps_each_page_to_its_actual_traits(n_traits, monkeypatch):
    """#466 review round 3: which traits landed on which page was previously only
    discoverable by opening the image and reading axis labels — page_traits must name them
    directly. #466 review round 4: the original version of this test only recomputed the
    same slicing formula the production code uses (checking the formula against itself); this
    verifies against the delegate's own rendered subplot titles instead, and additionally
    covers n_traits=64 (an exact multiple of batch_size) and n_traits=65 (one leftover trait
    alone on the last page) — boundary cases the original n_traits=60 doesn't exercise
    (#466 review round 5)."""
    wide_experiment = "wide.csv"
    reader = FakeReader()
    reader.add_experiment(wide_experiment, _wide_df(n_traits))
    _ports.configure(reader=reader, store=FakeResultStore())
    captured = {}
    real = plot_trait_boxplots_tool.create_trait_boxplots_by_genotype_batched

    def _spy(*a, **k):
        figs = real(*a, **k)
        captured["figs"] = figs
        return figs

    monkeypatch.setattr(
        plot_trait_boxplots_tool, "create_trait_boxplots_by_genotype_batched", _spy
    )
    try:
        result = plot_trait_boxplots(
            PlotTraitBoxplotsParams(experiment=wide_experiment)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    all_trait_cols = result.resolved_trait_columns
    expected_pages = _expected_pages(n_traits)
    assert len(result.page_traits) == expected_pages
    assert len(captured["figs"]) == expected_pages
    for i, fig in enumerate(captured["figs"], start=1):
        name = f"trait_boxplots_page{i}.png"
        assert result.page_traits[name] == _titled_traits(fig)
    all_paged = [t for traits in result.page_traits.values() for t in traits]
    # sorted() alone would pass on a duplicated/dropped trait pair, so pin the count too
    assert sorted(all_paged) == sorted(all_trait_cols)
    assert len(all_paged) == len(all_trait_cols)


def test_page_traits_single_entry_when_not_batched(injected_ports):
    result = _run()
    assert list(result.page_traits.keys()) == ["trait_boxplots.png"]
    assert result.page_traits["trait_boxplots.png"] == result.resolved_trait_columns


def test_resolved_trait_columns_recorded_in_result_and_manifest(injected_ports):
    """#466 review: the actual auto-detected trait list used to render/persist the PNG was
    previously never recorded — only its count (n_traits_plotted)."""
    _reader, store = injected_ports
    result = _run()
    from bloom_mcp import experiment_utils as eu

    expected = eu.detect_columns(_raw_df())["trait_cols"]
    assert result.resolved_trait_columns == expected
    stored = store.get_run(_EXPERIMENT, "trait_boxplots", "latest")
    assert stored.params["resolved_trait_columns"] == expected


# ── tools/list presence ──────────────────────────────────────────────────────


def test_appears_in_tools_list():
    import asyncio

    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "sleap_roots_plot_trait_boxplots" in tools
    assert tools["sleap_roots_plot_trait_boxplots"].inputSchema is not None


# ── schema round-trip ────────────────────────────────────────────────────────


def test_valid_input_output_round_trip(injected_ports):
    result = _run()
    again = PlotTraitBoxplotsResult.model_validate(json.loads(result.model_dump_json()))
    assert again.n_traits_plotted == result.n_traits_plotted


def test_missing_experiment_is_invalid_input():
    with pytest.raises(BloomMCPError) as exc:
        plot_trait_boxplots({})
    assert exc.value.code == "invalid_input"


def test_unknown_field_is_rejected():
    """extra="forbid" (#466 review round 5): an unknown field isn't currently
    exploitable — it would be dropped before persistence either way — but silently
    accepting it masks a caller typo."""
    with pytest.raises(BloomMCPError) as exc:
        plot_trait_boxplots({"experiment": _EXPERIMENT, "trait_column": ["t1"]})
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
    df = _raw_df()
    from bloom_mcp import experiment_utils as eu

    trait = eu.detect_columns(df)["trait_cols"][0]
    with pytest.raises(BloomMCPError) as exc:
        _run(trait_columns=[trait, trait])
    assert exc.value.code == "invalid_input"
    assert trait in exc.value.message


def test_metadata_only_frame_with_no_traits_is_invalid_input():
    """A frame with a detectable genotype but no numeric trait: the genotype guard passes,
    so this must reach (and be rejected by) the trait-resolution guard."""
    df = pd.DataFrame(
        {"Barcode": ["b0", "b1"], "geno": ["g1", "g2"], "note": ["x", "y"]}
    )
    reader = FakeReader()
    reader.add_experiment("meta_only.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        with pytest.raises(BloomMCPError) as exc:
            plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="meta_only.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert exc.value.code == "invalid_input"


def test_reads_raw_even_when_a_cleaned_version_already_exists():
    reader = FakeReader()
    raw = _raw_df()
    reader.add_experiment(_EXPERIMENT, raw)
    cleaned = raw.copy()
    cleaned["Root_Biomass_mg"] = 0.0
    reader.add_cleaned_version(_EXPERIMENT, "v1", cleaned)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment=_EXPERIMENT))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert result.source == "raw"


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
    real = plot_trait_boxplots_tool.create_trait_boxplots_by_genotype

    def _spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(
        plot_trait_boxplots_tool, "create_trait_boxplots_by_genotype", _spy
    )
    with pytest.raises(BloomMCPError) as exc:
        plot_trait_boxplots(PlotTraitBoxplotsParams(experiment=bad))
    assert exc.value.code == "invalid_input"
    assert calls["n"] == 0


# ── provenance + links ───────────────────────────────────────────────────────


def test_provenance_stamped_seed_none_and_links_returned(injected_ports):
    _reader, store = injected_ports
    result = _run()

    stored = store.get_run(_EXPERIMENT, "trait_boxplots", "latest")
    assert stored.tool == "plot_trait_boxplots"
    assert stored.seed is None

    assert result.run_ref == stored.run_ref
    assert set(result.output_links) == set(result.outputs)
    for name, key in result.outputs.items():
        link = result.output_links[name]
        assert link.key == key
        assert link.url
        assert link.sha256 == stored.output_sha256[name]


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

    assert plot_trait_boxplots_tool.FIGURE_REGISTRY_LOCK is _plots.FIGURE_REGISTRY_LOCK


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

    real_delegate = plot_trait_boxplots_tool.create_trait_boxplots_by_genotype
    held: list[bool] = []

    def _spy(*a, **k):
        held.append(_plots.FIGURE_REGISTRY_LOCK.locked())
        return real_delegate(*a, **k)

    monkeypatch.setattr(
        plot_trait_boxplots_tool, "create_trait_boxplots_by_genotype", _spy
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

    monkeypatch.setattr(plot_trait_boxplots_tool, "call_with_figure_cleanup", _spy)
    _run()
    assert calls["n"] == 1


def test_mid_batch_delegate_raise_leaks_no_figures(monkeypatch):
    """#725: a *_batched delegate that has already rendered pages 1..N-1 and then fails
    on page N returned nothing, so `figures` is still [] in `finally` and those pages were
    unreachable — a real leak until creation moved under `call_with_figure_cleanup`, whose
    fignum diff closes exactly the figures registered during the failed call."""
    import matplotlib.pyplot as plt

    wide_experiment = "wide.csv"
    reader = FakeReader()
    reader.add_experiment(wide_experiment, _wide_df(60))  # > TRAIT_BATCH_THRESHOLD
    _ports.configure(reader=reader, store=FakeResultStore())
    try:

        def _two_pages_then_boom(*a, **k):
            plt.figure()
            plt.figure()
            raise RuntimeError("page 3 failed")

        monkeypatch.setattr(
            plot_trait_boxplots_tool,
            "create_trait_boxplots_by_genotype_batched",
            _two_pages_then_boom,
        )
        before = plt.get_fignums()
        with pytest.raises(BloomMCPError):
            plot_trait_boxplots(PlotTraitBoxplotsParams(experiment=wide_experiment))
        assert plt.get_fignums() == before
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_closes_figures_while_holding_the_figure_registry_lock(
    injected_ports, monkeypatch
):
    """The close runs under the lock — the round-6 gap this pins shut.

    Asserts the property directly (was the lock actually held at the moment
    ``plt.close`` ran?) rather than counting acquisitions, so a later refactor
    that still enters the lock twice but moves the close back outside it fails
    here rather than silently reopening the race (#466 review round 7).
    """
    real_lock = plot_trait_boxplots_tool.FIGURE_REGISTRY_LOCK
    real_close = plot_trait_boxplots_tool.plt.close
    held: list[bool] = []

    def _spy_close(fig):
        held.append(real_lock.locked())
        return real_close(fig)

    monkeypatch.setattr(plot_trait_boxplots_tool.plt, "close", _spy_close)
    _run()
    assert held, "the tool never closed a figure"
    assert all(held), "plt.close ran without FIGURE_REGISTRY_LOCK held"


# ── error envelope ───────────────────────────────────────────────────────────


def test_unresolvable_experiment_errors_with_no_run(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError):
        plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="does_not_exist.csv"))
    assert store.list_runs("does_not_exist.csv", "trait_boxplots") == []


def test_delegate_raise_is_structured_without_leaking(injected_ports, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(
        plot_trait_boxplots_tool, "create_trait_boxplots_by_genotype", _boom
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
        plot_trait_boxplots_tool, "create_trait_boxplots_by_genotype", _boom
    )

    with pytest.raises(BloomMCPError):
        _run()
    assert store.list_runs(_EXPERIMENT, "trait_boxplots") == []
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
    store.fail_next_commit(_EXPERIMENT, "trait_boxplots")
    with pytest.raises(BloomMCPError):
        _run()
    assert store.list_runs(_EXPERIMENT, "trait_boxplots") == []
    assert not captured["staging_dir"].exists()


# ── ResultStore write-path failures surface as tool_error, not a bare internal_error ref
# (#640/#466 review) ──────────────────────────────────────────────────────────


def test_commit_failure_surfaces_as_tool_error(injected_ports):
    _reader, store = injected_ports
    store.fail_next_commit(_EXPERIMENT, "trait_boxplots")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "commit failed for trait_boxplots" in exc.value.message


def test_manifest_read_failure_surfaces_as_tool_error(injected_ports):
    _reader, store = injected_ports
    store.fail_next_read(_EXPERIMENT, "trait_boxplots")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "manifest read failure" in exc.value.message


# ── per-group sample-size disclosure (#748) ──────────────────────────────────

_CSV_NAME = "group_sample_sizes.csv"


def _captured_outputs(store, monkeypatch, suffix=".csv"):
    """Capture committed files before ``commit`` rmtree's the staging dir (see the histogram
    twin). Filtered by suffix so a bare ``read_text`` never hits a ``.png``."""
    captured: dict[str, str] = {}
    real_commit = store.commit

    def _spy(run, outputs):
        for name in outputs:
            if name.endswith(suffix):
                captured[name] = (run.staging_dir / name).read_text(encoding="utf-8")
        return real_commit(run, outputs)

    monkeypatch.setattr(store, "commit", _spy)
    return captured


def _loop_oracle(df, trait_cols, genotype_col):
    """Structurally different oracle: a Python loop, not production's vectorized groupby."""
    out = {}
    for g in sorted(x for x in df[genotype_col].dropna().unique()):
        sub = df[df[genotype_col] == g]
        for t in trait_cols:
            out[(t, str(g))] = int(len(sub[t].dropna()))
    return out


def _disclosure_experiment(reader, name="disclosure.csv"):
    """Hand-built frame exercising every bucket at once.

    t_healthy : A=3 B=3        -> both unflagged at floor 5? no: 3 < 5, both small
    t_thin    : A=1 B=6        -> A small, B unflagged
    t_absent  : A=0 B=6        -> A absent, B unflagged
    t_dead    : all null       -> no_data trait (collapsed, no per-group rows)
    t_inf     : A has one inf  -> A non-finite
    """
    df = pd.DataFrame(
        {
            "geno": ["A"] * 6 + ["B"] * 6 + [None],
            "t_thin": [1.0] + [float("nan")] * 5 + [1.0, 2, 3, 4, 5, 6] + [9.0],
            "t_absent": [float("nan")] * 6 + [1.0, 2, 3, 4, 5, 6] + [9.0],
            "t_dead": [float("nan")] * 13,
            "t_inf": [1.0, float("inf"), 3.0, 4.0, 5.0, 6.0]
            + [1.0, 2, 3, 4, 5, 6]
            + [9.0],
        }
    )
    reader.add_experiment(name, df)
    return df


def _run_disclosure(reader, name="disclosure.csv"):
    return plot_trait_boxplots(PlotTraitBoxplotsParams(experiment=name))


def test_group_sample_sizes_match_hand_written_expectations(injected_ports):
    reader, _store = injected_ports
    df = _disclosure_experiment(reader)
    result = _run_disclosure(reader)

    assert result.n_rows_read == 13
    assert result.n_genotype_groups == 2
    assert result.rows_missing_genotype == 1  # never appears in any box
    expected = _loop_oracle(df, result.resolved_trait_columns, "geno")
    assert expected[("t_thin", "A")] == 1
    assert expected[("t_absent", "A")] == 0
    assert expected[("t_inf", "A")] == 6  # count() includes the inf


def test_absent_group_is_its_own_bucket(injected_ports):
    reader, _store = injected_ports
    _disclosure_experiment(reader)
    result = _run_disclosure(reader)

    absent = {(g.trait, g.genotype) for g in result.absent_genotype_groups}
    small = {(g.trait, g.genotype) for g in result.small_sample_groups}
    assert ("t_absent", "A") in absent
    assert ("t_absent", "A") not in small


def test_all_null_trait_collapses_to_no_data_traits(injected_ports):
    """One dead trait must not emit one absent row per genotype — at 19 genotypes that alone
    would exhaust the cap and evict every genuinely informative absence in the run."""
    reader, _store = injected_ports
    _disclosure_experiment(reader)
    result = _run_disclosure(reader)

    assert result.no_data_traits == ["t_dead"]
    assert result.no_data_trait_count == 1
    assert not [g for g in result.absent_genotype_groups if g.trait == "t_dead"]


def test_all_inf_cell_is_not_reported_as_a_healthy_box(injected_ports):
    """pandas count() treats +/-inf as present, so an inf-bearing cell clears a naive count
    floor while its drawn box has NaN quartiles (design.md Decision 5)."""
    reader, _store = injected_ports
    reader.add_experiment(
        "allinf.csv",
        pd.DataFrame(
            {
                "geno": ["A"] * 6 + ["B"] * 6,
                "t": [float("inf")] * 6 + [1.0, 2, 3, 4, 5, 6],
            }
        ),
    )
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="allinf.csv"))

    flagged = {(g.trait, g.genotype) for g in result.non_finite_groups}
    assert ("t", "A") in flagged
    assert result.non_finite_traits == ["t"]
    # It must not read as a healthy box: 6 inf values are not 6 observations.
    assert ("t", "A") not in {(g.trait, g.genotype) for g in result.small_sample_groups}
    assert result.box_n_min == 6  # only genotype B has finite data
    assert result.n_boxes_summarized == 1


def test_every_group_cell_lands_in_exactly_one_bucket(injected_ports):
    reader, _store = injected_ports
    _disclosure_experiment(reader)
    result = _run_disclosure(reader)

    buckets = [
        {(g.trait, g.genotype) for g in result.absent_genotype_groups},
        {(g.trait, g.genotype) for g in result.non_finite_groups},
        {(g.trait, g.genotype) for g in result.small_sample_groups},
    ]
    claimed = [cell for b in buckets for cell in b]
    assert len(claimed) == len(set(claimed)), "a cell is claimed by two buckets"
    # No bucket may claim a cell of a collapsed no-data trait.
    assert not [cell for cell in claimed if cell[0] in result.no_data_traits]
    # Totality: drawn + absent + (collapsed dead traits x groups) == every cell.
    total_cells = result.n_traits_plotted * result.n_genotype_groups
    assert (
        result.n_boxes_drawn
        + result.absent_genotype_group_count
        + result.no_data_trait_count * result.n_genotype_groups
        == total_cells
    )


@pytest.mark.parametrize(
    "n,bucket", [(0, "absent"), (1, "small"), (4, "small"), (5, None)]
)
def test_bucket_boundaries(injected_ports, n, bucket):
    reader, _store = injected_ports
    # Genotype A gets n finite values for t; B always has a full column so the trait is
    # never collapsed as a no-data trait.
    values = [float(i) for i in range(n)] + [float("nan")] * (6 - n)
    reader.add_experiment(
        f"boundary_{n}.csv",
        pd.DataFrame(
            {"geno": ["A"] * 6 + ["B"] * 6, "t": values + [1.0, 2, 3, 4, 5, 6]}
        ),
    )
    result = plot_trait_boxplots(
        PlotTraitBoxplotsParams(experiment=f"boundary_{n}.csv")
    )
    absent = {(g.trait, g.genotype) for g in result.absent_genotype_groups}
    small = {(g.trait, g.genotype) for g in result.small_sample_groups}
    assert (("t", "A") in absent) == (bucket == "absent")
    assert (("t", "A") in small) == (bucket == "small")


def _many_thin_groups(reader, name="manythin.csv"):
    """More flagged cells than the cap, every one tied at n=1, so the ordering is decided
    entirely by the tie-break."""
    n_geno = _viz_shared.MAX_FLAGGED_REPORTED + 5
    rows = {"geno": [], "t": [], "t_full": []}
    for i in range(n_geno):
        rows["geno"].extend([f"G{i:02d}"] * 6)
        rows["t"].extend([1.0] + [float("nan")] * 5)
        rows["t_full"].extend([float(j) for j in range(6)])
    reader.add_experiment(name, pd.DataFrame(rows))
    return n_geno


def test_small_sample_groups_are_ordered_by_ascending_count(injected_ports):
    """The cap's whole safety argument is that ascending order truncates the BEST-supported
    end. `_many_thin_groups` ties every cell at n=1, so an implementation that sorted
    descending — inverting that argument and truncating the worst-supported end — satisfied a
    sorted() assertion vacuously. This uses mixed counts and pins that the single worst cell
    survives truncation.
    """
    reader, _store = injected_ports
    n_over_cap = _viz_shared.MAX_FLAGGED_REPORTED + 2
    rows = {"geno": [], "t": []}
    for i in range(n_over_cap):
        # One cell at n=1; every other flagged cell at n=4. Descending order would truncate
        # the n=1 cell away entirely.
        n_obs = 1 if i == n_over_cap - 1 else 4
        rows["geno"].extend([f"G{i:02d}"] * 6)
        rows["t"].extend(
            [float(j) for j in range(n_obs)] + [float("nan")] * (6 - n_obs)
        )
    reader.add_experiment("mixed_thin.csv", pd.DataFrame(rows))
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="mixed_thin.csv"))

    counts = [g.n for g in result.small_sample_groups]
    assert len(counts) == _viz_shared.MAX_FLAGGED_REPORTED
    assert result.small_sample_group_count == n_over_cap
    assert counts == sorted(counts)
    assert counts[0] == 1, "the worst-supported box must survive the cap"
    assert result.small_sample_groups[0].genotype == f"G{n_over_cap - 1:02d}"
    assert result.box_n_min == 1


def test_small_sample_groups_capped_and_deterministic_on_ties(injected_ports):
    reader, _store = injected_ports
    n_geno = _many_thin_groups(reader)
    first = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="manythin.csv"))
    second = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="manythin.csv"))

    entries = [(g.trait, g.genotype, g.n) for g in first.small_sample_groups]
    assert len(entries) == _viz_shared.MAX_FLAGGED_REPORTED
    assert first.small_sample_group_count == n_geno  # uncapped truth survives
    assert [e[2] for e in entries] == sorted(e[2] for e in entries)
    assert entries == sorted(entries, key=lambda e: (e[2], e[0], e[1]))
    assert entries == [(g.trait, g.genotype, g.n) for g in second.small_sample_groups]


def test_box_n_summaries_exclude_absent_cells(injected_ports):
    """Including zero-count cells prints "median=0" on a run where every drawn box is
    healthy (design.md Decision 7)."""
    reader, _store = injected_ports
    # 3 genotypes x 3 traits; each trait is observed for exactly one genotype, at n=6.
    rows = {"geno": [], "t0": [], "t1": [], "t2": []}
    for i in range(3):
        rows["geno"].extend([f"G{i}"] * 6)
        for t in range(3):
            rows[f"t{t}"].extend(
                [float(j) for j in range(6)] if t == i else [float("nan")] * 6
            )
    reader.add_experiment("sparse.csv", pd.DataFrame(rows))
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="sparse.csv"))

    assert result.box_n_min == 6
    assert result.box_n_median == pytest.approx(6.0)
    assert result.box_n_max == 6
    assert result.n_boxes_summarized == 3
    assert result.n_boxes_drawn == 3
    assert result.absent_genotype_group_count == 6


def test_rows_with_null_genotype_are_counted_and_excluded(injected_ports):
    reader, _store = injected_ports
    df = _disclosure_experiment(reader)
    result = _run_disclosure(reader)
    assert result.rows_missing_genotype == 1
    assert result.n_rows_read == len(df)
    # That row's values reach no box.
    assert result.box_n_max == 6


def test_max_nan_fraction_excludes_absent_cells_and_breaks_ties_by_name(injected_ports):
    """An absent cell sits at nan_fraction 1.0 by construction, so including absent cells hands
    this field to a cell already fully reported in absent_genotype_groups — masking the case the
    field exists for: a box that CLEARS the count floor while most of its column is missing.
    """
    reader, _store = injected_ports
    _disclosure_experiment(reader)
    result = _run_disclosure(reader)

    # t_thin x A keeps 1 of 6 rows -> 0.833; t_absent x A is 1.0 but absent, so excluded.
    assert result.max_nan_fraction == pytest.approx(5 / 6, abs=1e-4)
    assert result.max_nan_fraction_group == ["t_thin", "A"]

    # Tie-break: two cells at the same fraction resolve by (trait, genotype).
    reader.add_experiment(
        "tied.csv",
        pd.DataFrame(
            {
                "geno": ["A"] * 6 + ["B"] * 6,
                "z_trait": [1.0] + [float("nan")] * 5 + [1.0] + [float("nan")] * 5,
                "a_trait": [1.0] + [float("nan")] * 5 + [1.0] + [float("nan")] * 5,
            }
        ),
    )
    tied = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="tied.csv"))
    assert tied.max_nan_fraction_group == ["a_trait", "A"]


def test_group_sample_sizes_csv_is_committed_and_complete(injected_ports, monkeypatch):
    reader, store = injected_ports
    df = _disclosure_experiment(reader)
    captured = _captured_outputs(store, monkeypatch)
    result = _run_disclosure(reader)

    assert _CSV_NAME in result.outputs
    assert _CSV_NAME in result.output_links
    assert _CSV_NAME not in result.page_traits
    table = pd.read_csv(pd.io.common.StringIO(captured[_CSV_NAME]))
    assert list(table.columns) == _viz_shared.GROUP_TABLE_COLUMNS
    rows = {(r["trait"], r["genotype"]): r for r in table.to_dict("records")}
    # Every (trait x genotype) cell, including collapsed dead traits and absent cells.
    assert len(rows) == result.n_traits_plotted * result.n_genotype_groups
    for (trait, geno), expected in _loop_oracle(
        df, result.resolved_trait_columns, "geno"
    ).items():
        assert rows[(trait, geno)]["n_plotted"] == expected
    assert rows[("t_inf", "A")]["n_non_finite"] == 1
    assert rows[("t_inf", "A")]["n_finite"] == 5
    assert rows[("t_absent", "A")]["n_rows_in_group"] == 6


def test_new_boxplot_fields_and_genotype_column_stamped_into_manifest_params(
    injected_ports,
):
    _reader, store = injected_ports
    result = _run()
    params = store.get_run(_EXPERIMENT, "trait_boxplots", "latest").params
    # genotype_column is auto-detected and data-dependent, and was recorded nowhere in the
    # manifest before #748 — without it every genotype label in the disclosure is
    # unreproducible from a stored run.
    assert params["genotype_column"] == result.genotype_column
    assert params["box_n_min"] == result.box_n_min
    assert params["box_n_median"] == result.box_n_median
    assert params["n_boxes_drawn"] == result.n_boxes_drawn
    assert params["small_sample_group_count"] == result.small_sample_group_count
    assert params["sample_size_note"] == result.sample_size_note
    assert params["rows_missing_genotype"] == result.rows_missing_genotype


def test_manifest_params_are_native_json_types(injected_ports):
    _reader, store = injected_ports
    _run()
    params = store.get_run(_EXPERIMENT, "trait_boxplots", "latest").params
    for key, value in params.items():
        assert (
            type(value).__module__ == "builtins" or value is None
        ), f"params[{key!r}] is {type(value)}, not a native type"


def _reject(token):
    raise AssertionError(f"non-finite JSON token in payload: {token}")


def test_result_and_manifest_are_strict_json(injected_ports):
    _reader, store = injected_ports
    result = _run()
    json.loads(result.model_dump_json(), parse_constant=_reject)
    params = store.get_run(_EXPERIMENT, "trait_boxplots", "latest").params
    json.loads(json.dumps(params), parse_constant=_reject)


def test_new_result_fields_stay_under_the_links_not_blobs_ceiling(injected_ports):
    """The family's "links, not blobs" contract, asserted over the fields THIS change adds.

    Deliberately not over the whole result: `resolved_trait_columns` and `page_traits` already
    exceed 5,000 chars at cylinder width (~24,862 for 846 real trait names) — a pre-existing
    overrun this change must not silently adopt as acceptable (tasks §6.1).
    """
    reader, _store = injected_ports
    _many_thin_groups(reader, "wide_thin.csv")
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="wide_thin.csv"))
    new_fields = [
        "small_sample_groups",
        "absent_genotype_groups",
        "non_finite_groups",
        "no_data_traits",
        "non_finite_traits",
        "sample_size_note",
    ]
    dumped = result.model_dump()
    for name in new_fields:
        assert len(str(dumped[name])) <= 5000, f"{name} exceeds the ceiling"


def test_all_null_genotype_column_completes_with_null_summaries(injected_ports):
    """Reachable today — the tool guards only `genotype_col is None`, and the delegate renders
    an all-null genotype column successfully. min()/median() over the resulting empty groupby
    would raise / produce NaN, turning a succeeding run into a crash (design.md Decision 8).
    """
    reader, _store = injected_ports
    reader.add_experiment(
        "nullgeno.csv",
        pd.DataFrame(
            {"geno": [None] * 6, "t": [float(i) for i in range(6)]},
        ),
    )
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="nullgeno.csv"))
    assert result.n_genotype_groups == 0
    assert result.rows_missing_genotype == result.n_rows_read == 6
    assert result.box_n_min is None
    assert result.box_n_median is None
    assert result.box_n_max is None
    assert result.n_boxes_drawn == 0
    assert "no box" in result.sample_size_note.lower()
    json.loads(result.model_dump_json(), parse_constant=_reject)


# ── the rendered image (#748) ───────────────────────────────────────────────


def _captured_figure(monkeypatch, batched=False):
    name = (
        "create_trait_boxplots_by_genotype_batched"
        if batched
        else "create_trait_boxplots_by_genotype"
    )
    captured = {}
    real = getattr(plot_trait_boxplots_tool, name)

    def _spy(*a, **k):
        out = real(*a, **k)
        captured["figs"] = list(out) if batched else [out]
        return captured["figs"] if batched else out

    monkeypatch.setattr(plot_trait_boxplots_tool, name, _spy)
    return captured


def _unwrapped(text):
    """Collapse the presentational line wrapping `_draw_sample_size_note` applies.

    The note is wrapped only for rendering; the stamped/reported string is the unwrapped
    content, so comparisons between what is drawn and what is recorded normalize whitespace.
    """
    return " ".join(text.split())


def _note_texts(fig):
    """Figure-level texts that are OUR note. Filters rather than counts: the delegate already
    puts a suptitle in fig.texts on the vertical unbatched path and on every batched page.
    """
    return [t.get_text() for t in fig.texts if "rows per box" in t.get_text()]


@pytest.mark.parametrize("n_geno", [2, 10])
def test_each_genotype_tick_label_carries_its_own_n(
    injected_ports, monkeypatch, n_geno
):
    """The delegate switches to a horizontal orientation above 8 genotypes, moving the group
    labels from the x-axis to the y-axis, so both paths are exercised.

    Every genotype gets a DISTINCT count: an earlier version of this test gave every cell the
    same n, so an implementation that paired G00's label with G05's count — the exact
    mislabelling the by-text matching exists to rule out — passed it unchanged.
    """
    reader, _store = injected_ports
    expected = {}
    rows = {"geno": [], "t": []}
    for i in range(n_geno):
        # 3, 7, 9, 3, 7, 9, ... distinct within any pair, and never all equal.
        n_obs = (3, 7, 9)[i % 3]
        expected[f"G{i:02d}"] = n_obs
        rows["geno"].extend([f"G{i:02d}"] * 9)
        rows["t"].extend(
            [float(j) for j in range(n_obs)] + [float("nan")] * (9 - n_obs)
        )
    reader.add_experiment(f"ticks_{n_geno}.csv", pd.DataFrame(rows))
    captured = _captured_figure(monkeypatch)
    result = plot_trait_boxplots(
        PlotTraitBoxplotsParams(experiment=f"ticks_{n_geno}.csv")
    )

    assert result.box_labels_annotated is True
    fig = captured["figs"][0]
    ax = next(a for a in fig.axes if a.get_visible() and a.get_title() == "t")
    labels = [
        t.get_text()
        for axis in (ax.xaxis, ax.yaxis)
        for t in axis.get_ticklabels()
        if t.get_text().startswith("G")
    ]
    assert len(labels) == n_geno
    # Assert the PAIRING, not just the suffix shape.
    assert {
        label.split(" (n=")[0]: int(label.split("(n=")[1].rstrip(")"))
        for label in labels
    } == expected


def test_a_dead_trait_does_not_abandon_annotation_of_the_other_panels(
    injected_ports, monkeypatch
):
    """The delegate's "No data" panel has a real trait title over default numeric ticks. An
    earlier version returned at the first such panel, which (a) left every panel after it
    unannotated, (b) left panels before it annotated while reporting False, and (c) made the
    outcome depend on trait ordering. Both orderings must now annotate the live trait fully.

    At cylinder width the old behavior also made the flag useless: one dead trait anywhere in
    an 846-trait run reported False for all 53 pages while ~52 were correctly annotated.
    """
    reader, _store = injected_ports
    for order in (["a_live", "z_dead"], ["z_dead", "a_live"]):
        cols = {
            "a_live": [float(i % 6) for i in range(18)],
            "z_dead": [float("nan")] * 18,
        }
        df = pd.DataFrame(
            {"geno": [f"G{i % 3}" for i in range(18)], **{c: cols[c] for c in order}}
        )
        name = f"dead_{order[0]}.csv"
        reader.add_experiment(name, df)
        captured = _captured_figure(monkeypatch)
        result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment=name))

        assert result.no_data_traits == ["z_dead"]
        # A dead trait is not an annotation failure: there is no box there to label.
        assert result.box_labels_annotated is True, order
        fig = captured["figs"][0]
        live = next(
            a for a in fig.axes if a.get_visible() and a.get_title() == "a_live"
        )
        labels = [t.get_text() for t in live.xaxis.get_ticklabels()]
        assert labels == ["G0 (n=6)", "G1 (n=6)", "G2 (n=6)"], (order, labels)
        dead = next(
            a for a in fig.axes if a.get_visible() and a.get_title() == "z_dead"
        )
        assert not any(
            "(n=" in t.get_text() for t in dead.xaxis.get_ticklabels()
        ), "the No-data panel's numeric ticks must be left alone"


def test_numeric_genotypes_do_not_put_sample_sizes_on_the_value_axis(
    injected_ports, monkeypatch
):
    """GENOTYPE_PATTERNS matches on column NAME with no dtype check and includes "accession",
    so an integer accession column is ordinary real data. With a loose subset match and no
    locator check, the trait VALUE axis' own ticks passed and the sample sizes were stamped
    onto the value scale — while reporting success. That is the "annotating the wrong box"
    outcome the by-text design exists to prevent.
    """
    reader, _store = injected_ports
    reader.add_experiment(
        "numeric_geno.csv",
        pd.DataFrame(
            {
                "accession": [i % 10 for i in range(60)],
                "t": [float(1 + i % 5) for i in range(60)],
            }
        ),
    )
    captured = _captured_figure(monkeypatch)
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="numeric_geno.csv"))

    assert result.genotype_column == "accession"
    assert result.box_labels_annotated is True
    ax = next(
        a for a in captured["figs"][0].axes if a.get_visible() and a.get_title() == "t"
    )
    from matplotlib.ticker import FixedLocator

    for axis in (ax.xaxis, ax.yaxis):
        labels = [t.get_text() for t in axis.get_ticklabels()]
        annotated = [label for label in labels if "(n=" in label]
        if isinstance(axis.get_major_locator(), FixedLocator):
            # The categorical axis: all ten genotypes, each annotated.
            assert len(annotated) == 10, labels
        else:
            # The continuous trait-value scale must be untouched.
            assert not annotated, labels


def test_unmatched_tick_labels_are_left_alone_and_reported(injected_ports, monkeypatch):
    """The relabel matches tick TEXT against the known genotypes, so it is self-checking: a
    delegate that stops labelling ticks with genotype values degrades to the note alone
    rather than mislabelling a box."""
    reader, _store = injected_ports
    real = plot_trait_boxplots_tool.create_trait_boxplots_by_genotype

    def _scrambled(*a, **k):
        fig = real(*a, **k)
        for ax in fig.axes:
            if ax.get_visible() and ax.get_title():
                ax.xaxis.set_ticklabels(
                    ["?"] * len(ax.xaxis.get_ticklabels()),
                )
                ax.yaxis.set_ticklabels(["?"] * len(ax.yaxis.get_ticklabels()))
        return fig

    monkeypatch.setattr(
        plot_trait_boxplots_tool, "create_trait_boxplots_by_genotype", _scrambled
    )
    result = _run()
    assert result.box_labels_annotated is False
    assert (
        result.sample_size_note
    )  # the note is still the authoritative on-image signal


def test_note_is_drawn_on_every_render_including_unflagged(injected_ports, monkeypatch):
    """The unflagged path is the one that draws nothing today — a note that appeared only on
    flagged runs would leave every other image as uninformative as before."""
    reader, _store = injected_ports
    rows = {"geno": [], "t": []}
    for g in ("A", "B"):
        rows["geno"].extend([g] * 8)
        rows["t"].extend([float(j) for j in range(8)])
    reader.add_experiment("healthy.csv", pd.DataFrame(rows))
    captured = _captured_figure(monkeypatch)
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="healthy.csv"))

    drawn = _note_texts(captured["figs"][0])
    assert len(drawn) == 1
    assert "⚠" not in drawn[0]
    assert "min=8" in drawn[0] and "max=8" in drawn[0]
    assert "2 box(es) with finite data" in drawn[0]
    # The unconditional tail: without it, "no warning" reads as "sample sizes adequate".
    assert "not thereby reliable" in drawn[0]
    assert result.sample_size_note == _unwrapped(drawn[0])


def test_note_is_drawn_before_savefig(injected_ports, monkeypatch):
    """The spec says the note is on the figure that was SAVED; a call spy on Figure.text
    cannot prove ordering."""
    import matplotlib.figure

    seen = {}
    real_savefig = matplotlib.figure.Figure.savefig

    def _spy(self, *a, **k):
        seen.setdefault("notes_at_save", len(_note_texts(self)))
        return real_savefig(self, *a, **k)

    monkeypatch.setattr(matplotlib.figure.Figure, "savefig", _spy)
    _run()
    assert seen["notes_at_save"] == 1


def test_flagged_note_names_groups_and_reports_the_fraction(
    injected_ports, monkeypatch
):
    reader, _store = injected_ports
    _disclosure_experiment(reader)
    captured = _captured_figure(monkeypatch)
    _run_disclosure(reader)

    drawn = _unwrapped(_note_texts(captured["figs"][0])[0])
    assert "⚠" in drawn
    assert "t_thin x A (n=1)" in drawn  # the thin group is named, with its count
    assert (
        "1 of 5 drawn box(es) below n=5" in drawn
    )  # a fraction, with its population named
    # The head's population and the clause's population differ whenever a cell holds only
    # inf, so each must name its own denominator rather than leaving two bare "box(es)".
    assert "box(es) with finite data" in drawn
    assert "drawn box(es)" in drawn
    # Rows excluded from every box are part of the accounting.
    assert "1 row(s) excluded from every box (null genotype)" in drawn
    assert _CSV_NAME in drawn  # points at the complete table


def test_note_name_list_is_capped_with_remainder(injected_ports, monkeypatch):
    reader, _store = injected_ports
    _many_thin_groups(reader)
    captured = _captured_figure(monkeypatch)
    plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="manythin.csv"))
    drawn = _note_texts(captured["figs"][0])[0]
    assert "more" in drawn
    assert drawn.count("(n=1)") <= _viz_shared.MAX_NOTE_NAMES


def test_paginated_notes_are_page_scoped(injected_ports, monkeypatch):
    """A 53-page cylinder render must not stamp page 1's thin groups onto page 37's note."""
    reader, store = injected_ports
    n_traits = 20
    rows = {"geno": ["A"] * 8 + ["B"] * 8}
    for t in range(n_traits):
        rows[f"trait_{t:02d}"] = [float(j) for j in range(8)] * 2
    df = pd.DataFrame(rows)
    # Exactly one thin group, on the second page (traits 16+).
    df.loc[df["geno"].eq("A"), "trait_17"] = [1.0] + [float("nan")] * 7
    reader.add_experiment("paged.csv", df)

    monkeypatch.setattr(_viz_shared, "TRAIT_BATCH_THRESHOLD", 8)
    monkeypatch.setattr(plot_trait_boxplots_tool, "TRAIT_BATCH_THRESHOLD", 8)
    captured = _captured_figure(monkeypatch, batched=True)
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="paged.csv"))

    assert result.batched is True
    notes = [_note_texts(fig)[0] for fig in captured["figs"]]
    flagged_pages = [i for i, note in enumerate(notes) if "⚠" in note]
    assert len(flagged_pages) == 1, notes
    assert "trait_17" in notes[flagged_pages[0]]
    assert all("this page" in note for note in notes)
    # The per-page strings are deliberately NOT stamped into params (53 pages x ~2.5 KB on
    # every cylinder version, in a manifest re-validated on every subsequent run), and each is
    # reconstructible from page_traits + the committed CSV. Only the run-wide note is stamped.
    params = store.get_run("paged.csv", "trait_boxplots", "latest").params
    assert "page_sample_size_notes" not in params
    assert params["sample_size_note"] == result.sample_size_note
    assert "this page" not in result.sample_size_note


def test_tight_layout_called_only_when_unbatched(injected_ports, monkeypatch):
    """The batched delegate already calls tight_layout itself (visualization.py:430); paying
    for it again on 53 cylinder pages costs ~25% per page and would break the smoke timeout.
    """
    import matplotlib.figure

    calls = {"n": 0}
    real = matplotlib.figure.Figure.tight_layout

    def _spy(self, *a, **k):
        calls["n"] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(matplotlib.figure.Figure, "tight_layout", _spy)
    _run()
    unbatched_calls = calls["n"]
    assert unbatched_calls >= 1

    reader, _store = injected_ports
    calls["n"] = 0
    reader.add_experiment("wide2.csv", _wide_df(60))
    plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="wide2.csv"))
    # The delegate's own internal calls are not ours to count, so assert we added none on top
    # of the per-page baseline: 60 traits -> 4 pages, each tight_laid_out once by the delegate.
    assert calls["n"] == _expected_pages(60)


def test_note_drawing_failure_cleans_staging_and_commits_nothing(
    injected_ports, monkeypatch
):
    """The existing render-failure test patches the DELEGATE; the note is drawn at a different
    site and has its own failure path."""
    _reader, store = injected_ports
    captured = {}
    real_create = store.create_run

    def _spy_create(*a, **k):
        run = real_create(*a, **k)
        captured["staging_dir"] = run.staging_dir
        return run

    monkeypatch.setattr(store, "create_run", _spy_create)
    monkeypatch.setattr(
        plot_trait_boxplots_tool,
        "_draw_sample_size_note",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("note boom")),
    )
    with pytest.raises(BloomMCPError):
        _run()
    assert not captured["staging_dir"].exists()
    assert store.list_runs(_EXPERIMENT, "trait_boxplots") == []


# ── non-finite values (#748) ────────────────────────────────────────────────


def test_boxplot_over_non_finite_trait_renders_and_discloses(
    injected_ports, monkeypatch
):
    """Unlike the histogram, the boxplot delegate does NOT raise: it draws a confidently
    placed but shifted median with a NaN upper quartile. Failing the run would be a
    regression — the figure is still useful for every unaffected group."""
    reader, _store = injected_ports
    _disclosure_experiment(reader)
    captured = _captured_figure(monkeypatch)
    result = _run_disclosure(reader)

    assert result.non_finite_traits == ["t_inf"]
    assert ("t_inf", "A") in {(g.trait, g.genotype) for g in result.non_finite_groups}
    assert result.non_finite_group_count == 1
    # The non-finite clause carries the FINITE n, because such a cell is excluded from the
    # "below n=5" clause and its thinness would otherwise go unstated on the image.
    assert "t_inf x A (1 inf, n=5)" in _unwrapped(_note_texts(captured["figs"][0])[0])


def test_non_finite_values_are_not_stripped_before_rendering(
    injected_ports, monkeypatch
):
    """Characterization guard: the wrapper must not quietly alter a pre-clean EDA view."""
    reader, _store = injected_ports
    _disclosure_experiment(reader)
    seen = {}
    real = plot_trait_boxplots_tool.create_trait_boxplots_by_genotype

    def _spy(df, *a, **k):
        seen["has_inf"] = bool(np.isinf(df["t_inf"].to_numpy(dtype="float64")).any())
        return real(df, *a, **k)

    monkeypatch.setattr(
        plot_trait_boxplots_tool, "create_trait_boxplots_by_genotype", _spy
    )
    _run_disclosure(reader)
    assert seen["has_inf"] is True


# ── delegate-behavior pins (guard Decisions 4 and 11) ───────────────────────


def test_batched_delegate_calls_tight_layout():
    """Pins the fact the tool relies on to justify NOT calling tight_layout on batched pages."""
    import inspect

    from sleap_roots_analyze.visualization import (
        create_trait_boxplots_by_genotype_batched,
    )

    source = inspect.getsource(create_trait_boxplots_by_genotype_batched)
    assert "tight_layout" in source


@pytest.mark.parametrize("n_geno", [2, 10])
def test_delegate_draws_fliers_on_both_orientation_paths(n_geno):
    """design.md Decision 11's "nothing is hidden, so nothing to disclose" about outlier
    handling rests on matplotlib's showfliers=True/whis=1.5 defaults holding across TWO
    different APIs — pandas' DataFrame.boxplot below 9 genotypes, Axes.boxplot above."""
    from sleap_roots_analyze.visualization import create_trait_boxplots_by_genotype

    rows = {"geno": [], "t": []}
    for i in range(n_geno):
        rows["geno"].extend([f"G{i:02d}"] * 8)
        # One extreme value per group guarantees a flier if fliers are drawn at all.
        rows["t"].extend([1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 99.0])
    fig = create_trait_boxplots_by_genotype(
        pd.DataFrame(rows), ["t"], genotype_col="geno"
    )
    try:
        ax = next(a for a in fig.axes if a.get_visible() and a.get_title() == "t")
        fliers = [ln for ln in ax.lines if ln.get_marker() in ("o", "+", "d")]
        assert fliers, "no flier drawn — outlier points may now be hidden"
    finally:
        plt.close(fig)


def _maximal_note_experiment(reader, name="maximal.csv", n_traits=9, n_geno=19):
    """A frame that populates all four buckets with long, cylinder-length trait names — the
    worst realistic case for note height, which is what the reservation has to survive.
    """
    rng = np.random.default_rng(0)
    rows = {"geno": [f"GH_{7000 + i % n_geno}" for i in range(n_geno * 8)]}
    for t in range(n_traits):
        rows[f"Total.Root.Length.Trait.Number.{t}.mm"] = rng.normal(size=n_geno * 8)
    df = pd.DataFrame(rows)
    cols = [c for c in df.columns if c != "geno"]
    for i, c in enumerate(cols[:3]):
        df.loc[df["geno"].eq(f"GH_{7000 + i}"), c] = np.nan  # absent
    for i, c in enumerate(cols[3:6]):
        idx = df.index[df["geno"].eq(f"GH_{7010 + i}")][:-2]
        df.loc[idx, c] = np.nan  # small
    for c in cols[6:8]:
        df.loc[df.index[:3], c] = np.inf  # non-finite
    df[cols[8]] = np.nan  # no-data trait
    reader.add_experiment(name, df)
    return df


def _note_overlap_px(fig):
    """Pixels by which the drawn note intrudes into the lowest axes (<=0 means clear).

    Measured against ``get_tightbbox()``, not ``get_window_extent()``: the latter is the axes
    rectangle alone, and each axes' tick labels and x-label hang BELOW it — a note can clear
    the rectangle and still land on the bottom row's axis labels, which is what an earlier
    fix did.
    """
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    note = next(t for t in fig.texts if "rows per box" in t.get_text())
    box = note.get_window_extent(renderer)
    lowest = min(
        a.get_tightbbox(renderer).y0
        for a in fig.axes
        if a.get_visible() and a.get_title()
    )
    return box.y1 - lowest


def test_note_never_overlaps_the_axes(injected_ports, monkeypatch):
    """The note is unbounded in height (up to four clauses x MAX_NOTE_NAMES names, wrapped), so
    a fixed reservation cannot hold it. Drawn at a fixed offset it sat ON the bottom row of
    boxes: bbox_inches="tight" grew the canvas so nothing was clipped, but the axes never moved.

    The committed snapshot baseline cannot catch this — turface_19 is unflagged by design, so
    its note is one line — which is why this asserts geometry directly.
    """
    reader, _store = injected_ports
    _maximal_note_experiment(reader)
    captured = _captured_figure(monkeypatch)
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="maximal.csv"))

    drawn = _note_texts(captured["figs"][0])[0]
    assert drawn.count("\n") + 1 >= 5, "fixture must produce a genuinely tall note"
    assert "⚠" in result.sample_size_note
    assert _note_overlap_px(captured["figs"][0]) <= 0


def test_note_never_overlaps_the_axes_when_batched(injected_ports, monkeypatch):
    """The batched path skips tight_layout (the delegate already ran it), so nothing there
    reserved space at all — and cylinder, at 846 traits, always takes this path."""
    reader, _store = injected_ports
    _maximal_note_experiment(reader, "maximal_wide.csv", n_traits=20, n_geno=19)
    monkeypatch.setattr(_viz_shared, "TRAIT_BATCH_THRESHOLD", 8)
    monkeypatch.setattr(plot_trait_boxplots_tool, "TRAIT_BATCH_THRESHOLD", 8)
    captured = _captured_figure(monkeypatch, batched=True)
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="maximal_wide.csv"))

    assert result.batched is True
    for i, fig in enumerate(captured["figs"]):
        assert _note_overlap_px(fig) <= 0, f"page {i + 1}"


def test_note_is_not_parsed_as_mathtext(injected_ports, monkeypatch):
    """This is the first place the codebase concatenates up to 40 data-derived names into one
    Text object, so two names each carrying a "$" pair up: matplotlib renders the disclosure as
    italicised mathtext, or — with a brace or backslash between them — raises
    ParseFatalException and fails the run after create_run. The delegate's own tick labels are
    separate Text objects and never had this exposure.
    """
    reader, _store = injected_ports
    reader.add_experiment(
        "mathtext.csv",
        pd.DataFrame(
            {
                "geno": ["A"] * 6 + ["B"] * 6,
                # Two "$" and a brace between them: mathtext would fail to parse this.
                "cost_$_per_{unit}": [1.0] + [float("nan")] * 5 + [1.0, 2, 3, 4, 5, 6],
                "yield_$_ok": [1.0] + [float("nan")] * 5 + [1.0, 2, 3, 4, 5, 6],
            }
        ),
    )
    captured = _captured_figure(monkeypatch)
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="mathtext.csv"))

    assert "cost_$_per_{unit}" in result.sample_size_note
    note = next(t for t in captured["figs"][0].texts if "rows per box" in t.get_text())
    assert note.get_parse_math() is False
    # The real proof: rendering must not raise.
    captured["figs"][0].canvas.draw()


def test_thin_box_count_covers_inf_carrying_boxes_the_buckets_exclude(injected_ports):
    """small_sample_groups excludes inf-carrying cells to keep the buckets mutually exclusive,
    so a box on 4 finite values plus 2 infs is reported only as non-finite. A caller gating on
    small_sample_group_count == 0 would conclude "no thin boxes" while box_n_min reads 4.
    """
    reader, _store = injected_ports
    reader.add_experiment(
        "thin_inf.csv",
        pd.DataFrame(
            {
                "geno": ["A"] * 6 + ["B"] * 6,
                "t": [1.0, 2.0, 3.0, 4.0, np.inf, np.inf] + [1.0, 2, 3, 4, 5, 6],
            }
        ),
    )
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="thin_inf.csv"))

    assert result.box_n_min == 4
    assert result.small_sample_group_count == 0  # excluded, by design
    assert result.non_finite_group_count == 1
    assert result.thin_box_count == 1  # ...but the thinness is still reported
    assert "n=4" in result.sample_size_note  # and named on the image


def test_non_finite_groups_are_ordered_worst_first(injected_ports):
    """Ordering by name would let a cell with one inf survive the cap while one with many is
    truncated away — inverting the same worst-first argument every other bucket rests on.
    """
    reader, _store = injected_ports
    n_over_cap = _viz_shared.MAX_FLAGGED_REPORTED + 2
    rows = {"geno": [], "t": []}
    for i in range(n_over_cap):
        # a_00 gets 1 inf; the LAST genotype by name gets the most.
        n_inf = 1 if i < n_over_cap - 1 else 5
        rows["geno"].extend([f"g{i:02d}"] * 8)
        rows["t"].extend([np.inf] * n_inf + [float(j) for j in range(8 - n_inf)])
    reader.add_experiment("many_inf.csv", pd.DataFrame(rows))
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="many_inf.csv"))

    counts = [g.n_non_finite for g in result.non_finite_groups]
    assert len(counts) == _viz_shared.MAX_FLAGGED_REPORTED
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 5, "the worst-affected cell must survive the cap"
    assert result.non_finite_group_count == n_over_cap


def test_non_finite_traits_reports_an_uncapped_count(injected_ports):
    """Every other capped list carries an uncapped count; this one did not, so a run with 500
    inf-carrying traits reported 20 and nothing said more existed."""
    reader, _store = injected_ports
    n_traits = _viz_shared.MAX_FLAGGED_REPORTED + 3
    rows = {"geno": ["A"] * 8 + ["B"] * 8}
    for t in range(n_traits):
        rows[f"t{t:02d}"] = (
            [np.inf] + [float(j) for j in range(7)] + [float(j) for j in range(8)]
        )
    reader.add_experiment("many_inf_traits.csv", pd.DataFrame(rows))
    result = plot_trait_boxplots(
        PlotTraitBoxplotsParams(experiment="many_inf_traits.csv")
    )
    assert len(result.non_finite_traits) == _viz_shared.MAX_FLAGGED_REPORTED
    assert result.non_finite_trait_count == n_traits


def test_high_cardinality_genotype_column_is_rejected_before_building_the_table(
    injected_ports,
):
    """GENOTYPE_PATTERNS matches on column NAME with no dtype or cardinality check and includes
    "accession", so a per-plant accession column auto-detects as the grouper. The caps in this
    change bound what reaches the caller's context; this bounds what the SERVER builds — at 846
    traits x 5,000 values the table is 4.2M rows / ~660 MB, which result_store then reads whole
    to hash it.
    """
    reader, store = injected_ports
    n_rows = 2000
    rows = {"accession": [f"plant_{i}" for i in range(n_rows)]}
    for t in range(200):
        rows[f"t{t:03d}"] = [float(i % 7) for i in range(n_rows)]
    reader.add_experiment("high_card.csv", pd.DataFrame(rows))

    with pytest.raises(BloomMCPError) as exc:
        plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="high_card.csv"))
    assert exc.value.code == "assumption_violated"
    assert "accession" in exc.value.message
    assert exc.value.remedy
    assert store.list_runs("high_card.csv", "trait_boxplots") == []


def test_genotypes_that_stringify_alike_are_not_mislabelled(
    injected_ports, monkeypatch
):
    """Integer 1 and string "1" are DISTINCT groupby keys that render the same tick text, so
    the label -> count lookup is ambiguous. Set equality alone does not catch it: both the tick
    set and the expected set collapse identically, and the n=3 group was confidently labelled
    with the n=7 group's count while reporting success.

    Not reachable through either ingestion path today (both go through ``pd.read_csv``, which
    produces type-uniform columns), but the guarantee this feature rests on is "never mislabel
    a box, only decline to label it" — so it is enforced rather than argued.
    """
    reader, _store = injected_ports
    reader.add_experiment(
        "collide.csv",
        pd.DataFrame(
            {
                "geno": [1] * 3 + ["1"] * 7 + [2] * 5,
                "t": [1.0, 2, 3] + [1.0, 2, 3, 4, 5, 6, 7] + [1.0, 2, 3, 4, 5],
            }
        ),
    )
    captured = _captured_figure(monkeypatch)
    result = plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="collide.csv"))

    assert result.box_labels_annotated is False
    ax = next(
        a for a in captured["figs"][0].axes if a.get_visible() and a.get_title() == "t"
    )
    labels = [t.get_text() for t in ax.xaxis.get_ticklabels()]
    assert not any("(n=" in label for label in labels), labels
    # The note remains the authoritative on-image signal.
    assert "rows per box" in result.sample_size_note


def test_tick_labels_are_not_parsed_as_mathtext(injected_ports, monkeypatch):
    """A genotype like "$a__b$" is self-contained mathtext: matplotlib raises at savefig — after
    create_run — rather than rendering it literally. The note-drawing path was guarded; the
    tick-label path was not."""
    reader, _store = injected_ports
    reader.add_experiment(
        "mathtext_geno.csv",
        pd.DataFrame(
            {
                "geno": ["$a__b$"] * 6 + ["plain"] * 6,
                "t": [float(j) for j in range(6)] * 2,
            }
        ),
    )
    captured = _captured_figure(monkeypatch)
    result = plot_trait_boxplots(
        PlotTraitBoxplotsParams(experiment="mathtext_geno.csv")
    )

    assert result.box_labels_annotated is True
    ax = next(
        a for a in captured["figs"][0].axes if a.get_visible() and a.get_title() == "t"
    )
    ticks = [t for t in ax.xaxis.get_ticklabels()]
    assert all(t.get_parse_math() is False for t in ticks)
    assert any("$a__b$ (n=6)" == t.get_text() for t in ticks)
    # The real proof: rendering must not raise.
    captured["figs"][0].canvas.draw()


def test_page_count_mismatch_fails_loudly(injected_ports, monkeypatch):
    """page_traits and every per-page note are derived from the slicing formula BEFORE
    rendering (they have to be — params are stamped at create_run). If the delegate's batch
    size ever drifts, a page's note would describe a different set of traits than the page
    shows, so this must fail rather than mislabel."""
    reader, store = injected_ports
    reader.add_experiment("drift.csv", _wide_df(60))
    real = plot_trait_boxplots_tool.create_trait_boxplots_by_genotype_batched

    def _one_page_short(*a, **k):
        figs = list(real(*a, **k))
        plt.close(figs.pop())  # delegate returns fewer pages than the formula predicts
        return figs

    monkeypatch.setattr(
        plot_trait_boxplots_tool,
        "create_trait_boxplots_by_genotype_batched",
        _one_page_short,
    )
    with pytest.raises(BloomMCPError) as exc:
        plot_trait_boxplots(PlotTraitBoxplotsParams(experiment="drift.csv"))
    assert exc.value.code == "internal_error"
    assert store.list_runs("drift.csv", "trait_boxplots") == []
