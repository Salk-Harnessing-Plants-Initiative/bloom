"""Contract + oracle tests for the contract-wrapped ``plot_trait_histograms`` tool (#466).

Converges the tool onto ``@as_mcp_tool`` — Pydantic I/O, structured ``BloomMCPError``, one
stamped ``Provenance``, versioned ``ResultStore`` persistence under its own tool class — mirroring
``qc_inspect``'s read-only, pre-clean EDA pattern. A batched render (above
``_viz_shared.TRAIT_BATCH_THRESHOLD`` traits) persists one committed output per page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import (
    FakeResultStore,
    SupabaseResultStore,
)
from bloom_mcp.tools import _ports
from bloom_mcp.sections.sleap_roots.analysis import _viz_shared
from bloom_mcp.sections.sleap_roots.analysis import (
    plot_trait_histograms as plot_trait_histograms_tool,
)
from bloom_mcp.sections.sleap_roots.analysis.plot_trait_histograms import (
    PlotTraitHistogramsParams,
    PlotTraitHistogramsResult,
    plot_trait_histograms,
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


def _run(**overrides) -> PlotTraitHistogramsResult:
    return plot_trait_histograms(
        PlotTraitHistogramsParams(experiment=_EXPERIMENT, **overrides)
    )


# ── delegation pinning + batching boundary ──────────────────────────────────


def test_delegates_unbatched_below_threshold(injected_ports, monkeypatch):
    calls = {"n": 0}
    real = plot_trait_histograms_tool.create_trait_histograms

    def _spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(plot_trait_histograms_tool, "create_trait_histograms", _spy)
    result = _run()
    assert calls["n"] == 1
    assert result.batched is False
    assert result.n_pages == 1


def test_batches_above_threshold(monkeypatch):
    wide_experiment = "wide.csv"
    n_traits = 60
    reader = FakeReader()
    reader.add_experiment(wide_experiment, _wide_df(n_traits))
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        calls = {"n": 0}
        real = plot_trait_histograms_tool.create_trait_histograms_batched

        def _spy(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(
            plot_trait_histograms_tool, "create_trait_histograms_batched", _spy
        )
        result = plot_trait_histograms(
            PlotTraitHistogramsParams(experiment=wide_experiment)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert calls["n"] == 1
    assert result.batched is True
    expected = _expected_pages(n_traits)
    assert result.n_pages == expected
    # One figure per page PLUS the committed sample-size table (#748): the table is an
    # output, not a page, so n_pages still counts only rendered figures.
    assert len(result.outputs) == expected + 1
    assert len(result.output_links) == expected + 1
    assert len(result.page_traits) == expected


def test_batched_commit_failing_partway_through_persists_nothing(monkeypatch):
    """The tool-layer analog of test_store_parity.py's generic partial-commit coverage: a
    commit failing after some (not all, not zero) pages of a real multi-page batch are
    recorded must still surface as tool_error and leave no discoverable/partial run —
    the spec's own claim for this scenario was previously only covered transitively via
    the generic store tests, not independently at the tool layer (#466 review)."""
    wide_experiment = "wide.csv"
    n_traits = 60
    reader = FakeReader()
    reader.add_experiment(wide_experiment, _wide_df(n_traits))
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        expected_pages = _expected_pages(n_traits)
        assert 0 < 2 < expected_pages  # confirms this is a genuine partial, not 0/all
        store.fail_next_commit(wide_experiment, "trait_histograms", after_outputs=2)

        captured = {}
        real_create = store.create_run

        def _spy_create(*a, **k):
            run = real_create(*a, **k)
            captured["staging_dir"] = run.staging_dir
            return run

        monkeypatch.setattr(store, "create_run", _spy_create)

        with pytest.raises(BloomMCPError) as exc:
            plot_trait_histograms(PlotTraitHistogramsParams(experiment=wide_experiment))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert exc.value.code == "tool_error"
    assert store.list_runs(wide_experiment, "trait_histograms") == []
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
        result = plot_trait_histograms(PlotTraitHistogramsParams(experiment=experiment))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert result.batched is expect_batched


def test_delegate_batch_size_matches_live_default():
    """Pins _DELEGATE_BATCH_SIZE against the live delegate signature so a future
    sleap-roots-analyze bump that changes its default is caught here, not silently
    desyncing page_traits' chunking from what actually landed on each rendered page."""
    import inspect

    default = (
        inspect.signature(plot_trait_histograms_tool.create_trait_histograms_batched)
        .parameters["batch_size"]
        .default
    )
    assert default == plot_trait_histograms_tool._DELEGATE_BATCH_SIZE


def _titled_traits(fig) -> list[str]:
    """Extract the trait name each subplot's title actually names — create_trait_histograms
    titles each axis f"{trait}\\n(n={count})" (verified against the live delegate). A page
    that doesn't exactly fill its n_cols=4 grid (e.g. one leftover trait alone on the last
    page) pads with extra, invisible, blank-titled axes — confirmed against the live
    delegate, not assumed — so those must be filtered out, not just all of fig.axes."""
    return [ax.get_title().split("\n")[0] for ax in fig.axes if ax.get_visible()]


@pytest.mark.parametrize(
    "n_traits", [60, 64, 65]
)  # 64: an exact multiple of batch_size (16); 65: one leftover trait alone on the last page
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
    real = plot_trait_histograms_tool.create_trait_histograms_batched

    def _spy(*a, **k):
        figs = real(*a, **k)
        captured["figs"] = figs
        return figs

    monkeypatch.setattr(
        plot_trait_histograms_tool, "create_trait_histograms_batched", _spy
    )
    try:
        result = plot_trait_histograms(
            PlotTraitHistogramsParams(experiment=wide_experiment)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    all_trait_cols = result.resolved_trait_columns
    expected_pages = _expected_pages(n_traits)
    assert len(result.page_traits) == expected_pages
    assert len(captured["figs"]) == expected_pages
    for i, fig in enumerate(captured["figs"], start=1):
        name = f"trait_histograms_page{i}.png"
        assert result.page_traits[name] == _titled_traits(fig)
    # Every trait appears on exactly one page.
    all_paged = [t for traits in result.page_traits.values() for t in traits]
    assert sorted(all_paged) == sorted(all_trait_cols)
    assert len(all_paged) == len(all_trait_cols)


def test_page_traits_single_entry_when_not_batched(injected_ports):
    result = _run()
    assert list(result.page_traits.keys()) == ["trait_histograms.png"]
    assert result.page_traits["trait_histograms.png"] == result.resolved_trait_columns


def test_resolved_trait_columns_recorded_in_result_and_manifest(injected_ports):
    """#466 review: the actual auto-detected trait list used to render/persist the PNG was
    previously never recorded — only its count (n_traits_plotted) — so a manifest read
    months later couldn't answer "exactly which traits produced this artifact" if source
    columns drifted."""
    _reader, store = injected_ports
    result = _run()
    from bloom_mcp import experiment_utils as eu

    expected = eu.detect_columns(_raw_df())["trait_cols"]
    assert result.resolved_trait_columns == expected
    stored = store.get_run(_EXPERIMENT, "trait_histograms", "latest")
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
    assert "sleap_roots_plot_trait_histograms" in tools
    assert tools["sleap_roots_plot_trait_histograms"].inputSchema is not None


# ── schema round-trip ────────────────────────────────────────────────────────


def test_valid_input_output_round_trip(injected_ports):
    result = _run()
    again = PlotTraitHistogramsResult.model_validate(
        json.loads(result.model_dump_json())
    )
    assert again.n_traits_plotted == result.n_traits_plotted


def test_missing_experiment_is_invalid_input():
    with pytest.raises(BloomMCPError) as exc:
        plot_trait_histograms({})
    assert exc.value.code == "invalid_input"


def test_unknown_field_is_rejected():
    """extra="forbid" (#466 review round 5): an unknown field isn't currently
    exploitable — it would be dropped before persistence either way — but silently
    accepting it masks a caller typo."""
    with pytest.raises(BloomMCPError) as exc:
        plot_trait_histograms({"experiment": _EXPERIMENT, "trait_column": ["t1"]})
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
    df = pd.DataFrame(
        {"Barcode": ["b0", "b1"], "geno": ["g1", "g2"], "note": ["x", "y"]}
    )
    reader = FakeReader()
    reader.add_experiment("meta_only.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        with pytest.raises(BloomMCPError) as exc:
            plot_trait_histograms(PlotTraitHistogramsParams(experiment="meta_only.csv"))
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
    real = plot_trait_histograms_tool.create_trait_histograms

    def _spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(plot_trait_histograms_tool, "create_trait_histograms", _spy)
    with pytest.raises(BloomMCPError) as exc:
        plot_trait_histograms(PlotTraitHistogramsParams(experiment=bad))
    assert exc.value.code == "invalid_input"
    assert calls["n"] == 0


# ── provenance + links ───────────────────────────────────────────────────────


def test_provenance_stamped_seed_none_and_links_returned(injected_ports):
    _reader, store = injected_ports
    result = _run()

    stored = store.get_run(_EXPERIMENT, "trait_histograms", "latest")
    assert stored.tool == "plot_trait_histograms"
    assert stored.seed is None

    assert result.run_ref == stored.run_ref
    assert set(result.output_links) == set(result.outputs)
    for name, key in result.outputs.items():
        link = result.output_links[name]
        assert link.key == key
        assert link.url
        assert link.sha256 == stored.output_sha256[name]


def test_reads_raw_even_when_a_cleaned_version_already_exists():
    reader = FakeReader()
    raw = _raw_df()
    reader.add_experiment(_EXPERIMENT, raw)
    cleaned = raw.copy()
    cleaned["Root_Biomass_mg"] = 0.0
    reader.add_cleaned_version(_EXPERIMENT, "v1", cleaned)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = plot_trait_histograms(
            PlotTraitHistogramsParams(experiment=_EXPERIMENT)
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert result.source == "raw"


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
        plot_trait_histograms_tool.FIGURE_REGISTRY_LOCK is _plots.FIGURE_REGISTRY_LOCK
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

    real_delegate = plot_trait_histograms_tool.create_trait_histograms
    held: list[bool] = []

    def _spy(*a, **k):
        held.append(_plots.FIGURE_REGISTRY_LOCK.locked())
        return real_delegate(*a, **k)

    monkeypatch.setattr(plot_trait_histograms_tool, "create_trait_histograms", _spy)
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

    monkeypatch.setattr(plot_trait_histograms_tool, "call_with_figure_cleanup", _spy)
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
            plot_trait_histograms_tool,
            "create_trait_histograms_batched",
            _two_pages_then_boom,
        )
        before = plt.get_fignums()
        with pytest.raises(BloomMCPError):
            plot_trait_histograms(PlotTraitHistogramsParams(experiment=wide_experiment))
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
    real_lock = plot_trait_histograms_tool.FIGURE_REGISTRY_LOCK
    real_close = plot_trait_histograms_tool.plt.close
    held: list[bool] = []

    def _spy_close(fig):
        held.append(real_lock.locked())
        return real_close(fig)

    monkeypatch.setattr(plot_trait_histograms_tool.plt, "close", _spy_close)
    _run()
    assert held, "the tool never closed a figure"
    assert all(held), "plt.close ran without FIGURE_REGISTRY_LOCK held"


# ── error envelope ───────────────────────────────────────────────────────────


def test_unresolvable_experiment_errors_with_no_run(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError):
        plot_trait_histograms(
            PlotTraitHistogramsParams(experiment="does_not_exist.csv")
        )
    assert store.list_runs("does_not_exist.csv", "trait_histograms") == []


def test_delegate_raise_is_structured_without_leaking(injected_ports, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(plot_trait_histograms_tool, "create_trait_histograms", _boom)
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

    monkeypatch.setattr(plot_trait_histograms_tool, "create_trait_histograms", _boom)

    with pytest.raises(BloomMCPError):
        _run()
    assert store.list_runs(_EXPERIMENT, "trait_histograms") == []
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
    store.fail_next_commit(_EXPERIMENT, "trait_histograms")
    with pytest.raises(BloomMCPError):
        _run()
    assert store.list_runs(_EXPERIMENT, "trait_histograms") == []
    assert not captured["staging_dir"].exists()


# ── ResultStore write-path failures surface as tool_error, not a bare internal_error ref
# (#640/#466 review) ──────────────────────────────────────────────────────────


def test_commit_failure_surfaces_as_tool_error(injected_ports):
    _reader, store = injected_ports
    store.fail_next_commit(_EXPERIMENT, "trait_histograms")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "commit failed for trait_histograms" in exc.value.message


def test_manifest_read_failure_surfaces_as_tool_error(injected_ports):
    _reader, store = injected_ports
    store.fail_next_read(_EXPERIMENT, "trait_histograms")
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "tool_error"
    assert "manifest read failure" in exc.value.message


# ── sample-size + missingness disclosure (#748) ──────────────────────────────

_CSV_NAME = "trait_sample_sizes.csv"


def _captured_outputs(store, monkeypatch, suffix=".csv"):
    """Capture committed files by name before ``commit`` rmtree's the staging dir.

    ``FakeResultStore.commit`` deletes ``run.staging_dir`` on success and synthesizes
    ``fake://`` links, so a committed CSV's bytes are unreachable afterwards — the same
    commit-spy pattern ``test_pca_analysis_tool`` and ``test_viz_snapshot`` use. Filtered by
    suffix so a bare ``read_text`` never hits a ``.png``.
    """
    captured: dict[str, str] = {}
    real_commit = store.commit

    def _spy(run, outputs):
        for name in outputs:
            if name.endswith(suffix):
                captured[name] = (run.staging_dir / name).read_text(encoding="utf-8")
        return real_commit(run, outputs)

    monkeypatch.setattr(store, "commit", _spy)
    return captured


def _expected_trait_counts(df, trait_cols):
    """Structurally different oracle: a Python loop, not production's own vectorized
    expression (the anti-pattern ``test_page_traits_maps_each_page_to_its_actual_traits``
    documents — checking a formula against itself)."""
    return {c: int(len(df[c].dropna())) for c in trait_cols}


def _gappy_experiment(reader):
    """5 rows: one full trait, one gappy trait, one entirely null trait."""
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(5)],
            "t_full": [1.0, 2.0, 3.0, 4.0, 5.0],
            "t_gappy": [1.0, None, None, None, 5.0],
            # float("nan"), not None: a column of None is object dtype and would not be
            # auto-detected as a numeric trait at all.
            "t_empty": [float("nan")] * 5,
        }
    )
    reader.add_experiment("gappy.csv", df)
    return df


def test_per_trait_plotted_n_and_missingness_reported(injected_ports):
    reader, _store = injected_ports
    df = _gappy_experiment(reader)
    result = plot_trait_histograms(PlotTraitHistogramsParams(experiment="gappy.csv"))

    # Hand-written expectations, not a recomputation of the production expression.
    assert result.n_rows_read == 5
    assert result.trait_n_min == 0
    assert result.trait_n_max == 5
    assert result.trait_n_median == pytest.approx(2.0)
    assert result.max_nan_fraction == pytest.approx(1.0)
    assert result.max_nan_fraction_trait == "t_empty"
    assert _expected_trait_counts(df, result.resolved_trait_columns) == {
        "t_full": 5,
        "t_gappy": 2,
        "t_empty": 0,
    }


def test_all_null_trait_is_named_with_zero_plotted_n(injected_ports):
    """Today the "No data" panel is discoverable only by opening the image."""
    reader, _store = injected_ports
    _gappy_experiment(reader)
    result = plot_trait_histograms(PlotTraitHistogramsParams(experiment="gappy.csv"))

    flagged = {t.trait: t for t in result.low_sample_traits}
    assert flagged["t_empty"].n_plotted == 0
    assert flagged["t_empty"].nan_fraction == pytest.approx(1.0)
    assert "t_gappy" in flagged  # 2 < MIN_PLOTTED_SAMPLES
    assert "t_full" not in flagged
    assert result.low_sample_trait_count == 2


def test_low_sample_traits_ordered_capped_and_deterministic(injected_ports):
    reader, _store = injected_ports
    n_thin = _viz_shared.MAX_FLAGGED_REPORTED + 5
    data = {"Barcode": [f"b{i}" for i in range(6)]}
    for i in range(n_thin):
        # Every thin trait ties at n=1: ordering must still be deterministic, which is only
        # true if the tie-break is the trait name (design.md Decision 2).
        data[f"thin_{i:02d}"] = [1.0] + [None] * 5
    data["fat"] = [float(i) for i in range(6)]
    reader.add_experiment("thin.csv", pd.DataFrame(data))

    first = plot_trait_histograms(PlotTraitHistogramsParams(experiment="thin.csv"))
    second = plot_trait_histograms(PlotTraitHistogramsParams(experiment="thin.csv"))

    names = [t.trait for t in first.low_sample_traits]
    assert len(names) == _viz_shared.MAX_FLAGGED_REPORTED
    assert first.low_sample_trait_count == n_thin  # uncapped truth survives the cap
    assert names == sorted(names)  # tie-break is the trait name
    assert names == [t.trait for t in second.low_sample_traits]
    # The summaries are computed over every trait, not the capped sample.
    assert first.trait_n_min == 1
    assert first.trait_n_max == 6


def test_trait_sample_sizes_csv_is_committed_and_complete(injected_ports, monkeypatch):
    reader, store = injected_ports
    df = _gappy_experiment(reader)
    captured = _captured_outputs(store, monkeypatch)
    result = plot_trait_histograms(PlotTraitHistogramsParams(experiment="gappy.csv"))

    assert _CSV_NAME in result.outputs
    assert _CSV_NAME in result.output_links
    assert _CSV_NAME not in result.page_traits  # a table is not a page
    rows = {
        r["trait"]: r
        for r in pd.read_csv(pd.io.common.StringIO(captured[_CSV_NAME])).to_dict(
            "records"
        )
    }
    assert set(rows) == set(result.resolved_trait_columns)
    for trait, expected in _expected_trait_counts(
        df, result.resolved_trait_columns
    ).items():
        assert rows[trait]["n_plotted"] == expected
        assert rows[trait]["n_missing"] == 5 - expected


def test_new_histogram_fields_stamped_into_manifest_params(injected_ports):
    _reader, store = injected_ports
    result = _run()
    stored = store.get_run(_EXPERIMENT, "trait_histograms", "latest")
    params = stored.params
    assert params["trait_n_min"] == result.trait_n_min
    assert params["trait_n_max"] == result.trait_n_max
    assert params["low_sample_trait_count"] == result.low_sample_trait_count
    assert params["n_rows_read"] == result.n_rows_read
    assert [t["trait"] for t in params["low_sample_traits"]] == [
        t.trait for t in result.low_sample_traits
    ]


def test_manifest_params_are_native_json_types(injected_ports):
    """np.int64/np.float64 in params raise PydanticSerializationError at the manifest write —
    this tool is one of the first to stamp numeric aggregates (design.md Decision 9)."""
    _reader, store = injected_ports
    _run()
    stored = store.get_run(_EXPERIMENT, "trait_histograms", "latest")
    for key, value in stored.params.items():
        assert (
            type(value).__module__ == "builtins" or value is None
        ), f"params[{key!r}] is {type(value)}, not a native type"


def _reject(token):
    raise AssertionError(f"non-finite JSON token in payload: {token}")


def test_result_and_manifest_are_strict_json(injected_ports):
    """This change reports fractions whose denominator can be zero and medians whose
    population can be empty — a bare NaN/Infinity token would be written by
    storage_backend._json_bytes (json.dumps defaults to allow_nan=True) and rejected by strict
    readers."""
    _reader, store = injected_ports
    result = _run()
    json.loads(result.model_dump_json(), parse_constant=_reject)
    stored = store.get_run(_EXPERIMENT, "trait_histograms", "latest")
    json.loads(json.dumps(stored.params), parse_constant=_reject)


def test_zero_row_frame_completes_with_null_summaries(injected_ports):
    """A zero-row frame is not excluded by resolve_trait_columns; min()/median() over an empty
    population must not raise or produce NaN (design.md Decision 8)."""
    reader, _store = injected_ports
    reader.add_experiment(
        "empty.csv",
        pd.DataFrame({"Barcode": pd.Series(dtype=str), "t": pd.Series(dtype=float)}),
    )
    result = plot_trait_histograms(PlotTraitHistogramsParams(experiment="empty.csv"))
    assert result.n_rows_read == 0
    # Every resolved trait still gets a panel, so the summaries are defined (and zero) rather
    # than null — unlike plot_trait_boxplots, where an absent cell is drawn as nothing at all.
    assert result.trait_n_min == 0
    assert result.trait_n_median == 0.0
    assert result.trait_n_max == 0
    assert result.max_nan_fraction == 0.0  # no rows means no missingness to report
    json.loads(result.model_dump_json(), parse_constant=_reject)


# ── non-finite values (#748) ────────────────────────────────────────────────


def _inf_experiment(reader, name="inf.csv"):
    reader.add_experiment(
        name,
        pd.DataFrame(
            {
                "Barcode": [f"b{i}" for i in range(6)],
                "t_ok": [float(i) for i in range(6)],
                "t_inf": [1.0, 2.0, float("inf"), 4.0, 5.0, 6.0],
            }
        ),
    )


def test_histogram_over_non_finite_trait_is_assumption_violated_naming_it(
    injected_ports, monkeypatch
):
    """matplotlib cannot bin a non-finite range, so this run already fails today — with the
    delegate's own ValueError, redacted into a message naming no trait and offering no remedy.
    The RED assertions are the two spies: the guard must fire BEFORE the delegate is called and
    BEFORE a run is created."""
    reader, store = injected_ports
    _inf_experiment(reader)
    delegate_calls = {"n": 0}
    real_delegate = plot_trait_histograms_tool.create_trait_histograms

    def _delegate_spy(*a, **k):
        delegate_calls["n"] += 1
        return real_delegate(*a, **k)

    monkeypatch.setattr(
        plot_trait_histograms_tool, "create_trait_histograms", _delegate_spy
    )
    create_calls = {"n": 0}
    real_create = store.create_run

    def _create_spy(*a, **k):
        create_calls["n"] += 1
        return real_create(*a, **k)

    monkeypatch.setattr(store, "create_run", _create_spy)

    with pytest.raises(BloomMCPError) as exc:
        plot_trait_histograms(PlotTraitHistogramsParams(experiment="inf.csv"))

    assert exc.value.code == "assumption_violated"
    assert "t_inf" in exc.value.message
    assert "t_ok" not in exc.value.message
    assert exc.value.remedy
    assert delegate_calls["n"] == 0
    assert create_calls["n"] == 0
    assert store.list_runs("inf.csv", "trait_histograms") == []


def test_histogram_non_finite_guard_fires_on_a_batched_selection_too(injected_ports):
    reader, store = injected_ports
    df = _wide_df(60)
    df["trait_7"] = [float("-inf")] * len(df)
    reader.add_experiment("wide_inf.csv", df)
    with pytest.raises(BloomMCPError) as exc:
        plot_trait_histograms(PlotTraitHistogramsParams(experiment="wide_inf.csv"))
    assert exc.value.code == "assumption_violated"
    assert "trait_7" in exc.value.message
    assert store.list_runs("wide_inf.csv", "trait_histograms") == []


def test_non_finite_guard_ignores_unselected_traits(injected_ports):
    """The guard covers the RESOLVED selection only — an inf in a trait the caller did not ask
    for is never rendered, so it must not fail the run."""
    reader, _store = injected_ports
    _inf_experiment(reader)
    result = plot_trait_histograms(
        PlotTraitHistogramsParams(experiment="inf.csv", trait_columns=["t_ok"])
    )
    assert result.resolved_trait_columns == ["t_ok"]


# ── delegate-behavior pin (guards Decision 4) ───────────────────────────────


def test_delegate_titles_each_panel_with_its_n(injected_ports, monkeypatch):
    """plot_trait_histograms' image is deliberately NOT given a sample-size note because the
    delegate already titles every panel f"{trait}\\n(n={count})". `_titled_traits` splits that
    suffix off before asserting, so nothing else in this file fails if it disappears — this
    pins it directly against the live delegate.

    Note the delegate titles the BARE trait name for an all-NaN "No data" panel, so this
    asserts the non-empty case.
    """
    reader, _store = injected_ports
    df = _gappy_experiment(reader)
    captured = {}
    real = plot_trait_histograms_tool.create_trait_histograms

    def _spy(*a, **k):
        fig = real(*a, **k)
        captured["fig"] = fig
        return fig

    monkeypatch.setattr(plot_trait_histograms_tool, "create_trait_histograms", _spy)
    plot_trait_histograms(PlotTraitHistogramsParams(experiment="gappy.csv"))

    titles = {
        ax.get_title().split("\n")[0]: ax.get_title()
        for ax in captured["fig"].axes
        if ax.get_visible() and ax.get_title()
    }
    assert titles["t_full"] == f"t_full\n(n={len(df['t_full'].dropna())})"
    assert titles["t_gappy"] == f"t_gappy\n(n={len(df['t_gappy'].dropna())})"


def test_histogram_render_gains_no_note(injected_ports, monkeypatch):
    """Characterization guard (green today, must stay green): the histogram image is left
    alone. Filters rather than counts — a batched histogram carries its own suptitle in
    fig.texts."""
    reader, _store = injected_ports
    _gappy_experiment(reader)
    captured = {}
    real = plot_trait_histograms_tool.create_trait_histograms

    def _spy(*a, **k):
        fig = real(*a, **k)
        captured["fig"] = fig
        return fig

    monkeypatch.setattr(plot_trait_histograms_tool, "create_trait_histograms", _spy)
    plot_trait_histograms(PlotTraitHistogramsParams(experiment="gappy.csv"))
    assert not [t for t in captured["fig"].texts if "n per box" in t.get_text().lower()]
