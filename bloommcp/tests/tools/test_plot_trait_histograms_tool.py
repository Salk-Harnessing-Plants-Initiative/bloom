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
    assert len(result.outputs) == expected
    assert len(result.output_links) == expected


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
    titles each axis f"{trait}\\n(n={count})" (verified against the live delegate)."""
    return [ax.get_title().split("\n")[0] for ax in fig.axes]


@pytest.mark.parametrize(
    "n_traits", [60, 64]
)  # 64: an exact multiple of batch_size (16)
def test_page_traits_maps_each_page_to_its_actual_traits(n_traits, monkeypatch):
    """#466 review round 3: which traits landed on which page was previously only
    discoverable by opening the image and reading axis labels — page_traits must name them
    directly. #466 review round 4: the original version of this test only recomputed the
    same slicing formula the production code uses (checking the formula against itself); this
    verifies against the delegate's own rendered subplot titles instead, and additionally
    covers n_traits=64 (an exact multiple of batch_size), the boundary case n_traits=60 (not a
    multiple) doesn't exercise."""
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
