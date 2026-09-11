"""Unit tests for the tool-agnostic ``_plots`` helpers.

These tests exercise ``validate_plot_keys``, ``generate_figures``, and
``close_figures`` in isolation — no live stack, no Supabase, no matplotlib
import on the validation path.
"""

from __future__ import annotations

import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.tools._plots import (
    FIGURE_REGISTRY_LOCK,
    apply_font_style,
    call_with_figure_cleanup,
    check_plot_style_ceiling,
    close_figures,
    generate_figures,
    validate_plot_keys,
)

_VALID = {"key_a", "key_b", "key_c"}


# ── validate_plot_keys ───────────────────────────────────────────────────────


def test_none_is_accepted_without_error():
    validate_plot_keys(None, _VALID)  # no exception


def test_valid_subset_is_accepted():
    validate_plot_keys(["key_a", "key_b"], _VALID)  # no exception


def test_full_list_is_accepted():
    validate_plot_keys(list(_VALID), _VALID)  # no exception


def test_unknown_key_raises_invalid_input_naming_it():
    with pytest.raises(BloomMCPError) as exc:
        validate_plot_keys(["not_real"], _VALID)
    assert exc.value.code == "invalid_input"
    assert "not_real" in exc.value.message


def test_multiple_unknown_keys_named_in_error():
    with pytest.raises(BloomMCPError) as exc:
        validate_plot_keys(["bad_a", "bad_b"], _VALID)
    assert exc.value.code == "invalid_input"
    assert "bad_a" in exc.value.message or "bad_b" in exc.value.message


def test_duplicate_key_raises_invalid_input_naming_duplicate():
    with pytest.raises(BloomMCPError) as exc:
        validate_plot_keys(["key_a", "key_a"], _VALID)
    assert exc.value.code == "invalid_input"
    assert "key_a" in exc.value.message


def test_empty_list_raises_invalid_input():
    with pytest.raises(BloomMCPError) as exc:
        validate_plot_keys([], _VALID)
    assert exc.value.code == "invalid_input"


# ── check_plot_style_ceiling ─────────────────────────────────────────────────


def test_check_plot_style_ceiling_none_is_accepted_without_error():
    check_plot_style_ceiling(None, field_name="plot_font_size", max_value=100)  # no exc


@pytest.mark.parametrize("value", [1, 50, 100])
def test_check_plot_style_ceiling_in_range_is_accepted(value):
    check_plot_style_ceiling(
        value, field_name="plot_font_size", max_value=100
    )  # no exc


@pytest.mark.parametrize(
    "value", [0, -1, 101, float("inf"), float("-inf"), float("nan")]
)
def test_check_plot_style_ceiling_out_of_range_names_value_and_ceiling(value):
    """The whole point of this helper vs. a Field(gt=0, le=...) constraint: the message
    must name the actual submitted value and the ceiling, not just a field name (#721).
    """
    with pytest.raises(BloomMCPError) as exc:
        check_plot_style_ceiling(value, field_name="plot_font_size", max_value=100)
    assert exc.value.code == "invalid_input"
    assert "plot_font_size" in exc.value.message
    assert "100" in exc.value.message
    assert repr(value) in exc.value.message


# ── generate_figures ─────────────────────────────────────────────────────────


def test_generate_figures_populates_caller_dict():
    figures: dict = {}
    generate_figures({"a": lambda: "fig_a", "b": lambda: "fig_b"}, figures)
    assert figures == {"a": "fig_a", "b": "fig_b"}


def test_generate_figures_partial_failure_leaves_prior_results_in_caller_dict():
    """Regression: a mid-generation exception must not discard figures already
    produced by earlier calls — the caller's dict (passed in, not returned) is
    the only thing ``close_figures`` can reach in ``finally``."""
    figures: dict = {}

    def _boom():
        raise RuntimeError("second plotter blew up")

    with pytest.raises(RuntimeError):
        generate_figures(
            {
                "first": lambda: "fig_first",
                "second": _boom,
                "third": lambda: "fig_third",
            },
            figures,
        )
    # "first" ran and was recorded before "second" raised; "third" never ran.
    assert figures == {"first": "fig_first"}


def test_generate_figures_partial_failure_then_close_leaves_no_open_figures():
    """End-to-end with real matplotlib figures: after a mid-generation failure,
    ``close_figures`` on the caller's dict must close everything that leaked in."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures: dict = {}

    def _boom():
        raise RuntimeError("second plotter blew up")

    with pytest.raises(RuntimeError):
        generate_figures({"first": lambda: plt.figure(), "second": _boom}, figures)

    assert list(figures) == ["first"]
    assert plt.get_fignums() != []  # the first figure is open before cleanup
    close_figures(figures)
    assert (
        plt.get_fignums() == []
    )  # closed via the same dict generate_figures populated


# ── call_with_figure_cleanup ─────────────────────────────────────────────────


def test_call_with_figure_cleanup_returns_fn_result_on_success():
    assert call_with_figure_cleanup(lambda: "result") == "result"


def test_call_with_figure_cleanup_closes_a_figure_allocated_then_abandoned():
    """#721 PR review round 4: the same leak `generate_figures` was fixed for, now
    verified directly against the shared helper itself — `qc_inspect.py`,
    `remove_outliers.py`, `clustering.py`, and the 5 legacy `plot_*` tools all rely on
    this exact behavior via their own `except Exception: return "<message>"` blocks,
    which would otherwise silently swallow the exception without closing whatever the
    delegate had already allocated mid-render."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def _allocate_then_boom():
        plt.figure()
        raise RuntimeError("delegate blew up after allocating its figure")

    with pytest.raises(RuntimeError):
        call_with_figure_cleanup(_allocate_then_boom)

    assert plt.get_fignums() == []


def test_call_with_figure_cleanup_closes_multiple_figures_from_a_batched_delegate():
    """A batched delegate (e.g. create_trait_histograms_batched) can allocate several
    figures before raising on, say, the third page — all of them must be closed, not
    just the first."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def _allocate_three_then_boom():
        plt.figure()
        plt.figure()
        plt.figure()
        raise RuntimeError("batched delegate blew up on the third page")

    with pytest.raises(RuntimeError):
        call_with_figure_cleanup(_allocate_three_then_boom)

    assert plt.get_fignums() == []


def test_call_with_figure_cleanup_does_not_close_figures_that_predate_the_call():
    """Only figures allocated *during* this call are the caller's responsibility —
    something already open before it started (e.g. a figure another, unrelated call is
    still legitimately using) must survive untouched."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pre_existing = plt.figure()
    try:

        def _allocate_then_boom():
            plt.figure()
            raise RuntimeError("blew up")

        with pytest.raises(RuntimeError):
            call_with_figure_cleanup(_allocate_then_boom)

        assert plt.get_fignums() == [pre_existing.number]
    finally:
        plt.close(pre_existing)


def test_generate_figures_closes_a_figure_allocated_then_abandoned_mid_call():
    """#721: unlike ``_boom`` above (which raises with zero figure allocation), a
    real-world failure — an invalid ``plot_cmap`` reaching ``ax.scatter(cmap=...)`` — can
    allocate a figure (``plt.subplots()``) and *then* raise from later in the same call,
    before the callable ever returns. That figure is never assigned into ``figures`` (the
    assignment ``figures[key] = fn()`` never completes), so ``close_figures`` — which only
    iterates that dict — can never reach it on its own. ``generate_figures`` itself must
    close it before propagating the exception."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figures: dict = {}

    def _allocate_then_boom():
        plt.figure()  # allocated ...
        raise RuntimeError(
            "plotter blew up after allocating its figure"
        )  # ... then lost

    with pytest.raises(RuntimeError):
        generate_figures({"only": _allocate_then_boom}, figures)

    assert figures == {}  # never recorded — the callable never returned
    assert plt.get_fignums() == []  # yet generate_figures already closed it


def test_generate_figures_calls_are_serialized_across_threads():
    """#721 PR review: matplotlib's pyplot figure registry (``Gcf.figs``) is a single
    process-global ``OrderedDict``, not thread-local, and FastMCP dispatches sync tool
    handlers via a thread pool (`bloom_mcp/result_store/_locks.py`'s own docstring
    documents the same fact for the same reason). Without ``FIGURE_REGISTRY_LOCK``
    serializing the whole ``generate_figures`` call, two concurrent ``umap_analysis``/
    ``pca_analysis`` calls could interleave — and the allocate-then-raise cleanup above,
    which detects "new since I started" purely by diffing that shared global registry,
    would have no way to tell its own orphaned figure apart from one a different,
    unrelated concurrent call just allocated, closing that other call's figure instead.

    Verified here without needing to actually trigger that corruption (timing-dependent
    and therefore flaky to assert on directly): two threads race to call
    ``generate_figures``, one with an artificially slow plotter. If calls are correctly
    serialized, the fast thread's plotter cannot run until the slow thread's entire call
    — not just its plotter, the whole ``generate_figures`` invocation — has returned.
    Without the lock, the fast thread's plotter reliably finishes first.
    """
    import threading
    import time

    order: list[str] = []
    slow_thread_started = threading.Event()

    def _slow_call():
        def _slow_plotter():
            slow_thread_started.set()
            time.sleep(0.2)
            order.append("slow-plotted")
            return object()

        generate_figures({"k": _slow_plotter}, {})
        order.append("slow-call-done")

    def _fast_call():
        slow_thread_started.wait(timeout=5)

        def _fast_plotter():
            order.append("fast-plotted")
            return object()

        generate_figures({"k": _fast_plotter}, {})

    t_slow = threading.Thread(target=_slow_call)
    t_fast = threading.Thread(target=_fast_call)
    t_slow.start()
    t_fast.start()
    t_slow.join(timeout=5)
    t_fast.join(timeout=5)

    assert order == ["slow-plotted", "slow-call-done", "fast-plotted"], (
        f"expected the fast call to be fully blocked until the slow call's "
        f"generate_figures returned, got order={order}"
    )


# ── close_figures ────────────────────────────────────────────────────────────


def test_close_figures_empty_dict_does_not_raise():
    close_figures({})  # no exception, no matplotlib import


def test_close_figures_does_not_raise_on_already_closed_figure():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure()
    plt.close(fig)  # pre-close
    close_figures({"k": fig})  # best-effort; must not raise


def test_close_figures_clears_open_figures():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure()
    assert fig.number in plt.get_fignums()
    close_figures({"k": fig})
    assert fig.number not in plt.get_fignums()


# ── apply_font_style ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _close_figures_opened_directly_via_pyplot():
    """These tests build figures via ``plt.subplots()`` directly (not through the tool's
    own ``close_figures`` cleanup path), so close whatever's left afterward — otherwise
    they'd leak into ``matplotlib.pyplot``'s global figure registry and fail unrelated
    ``plt.get_fignums() == []`` assertions in other test modules run in the same session.
    """
    yield
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.close("all")


def _styled_figure():
    """A real Figure with a title, x/y labels, tick labels, a titled legend, a
    figure-level suptitle, and a standalone annotation — matching real catalog-plot
    shapes: ``create_pca_biplot``'s ``ax.legend(title=color_by, ...)``, a
    ``fig.suptitle`` like ``create_umap_colored_by_top_traits`` sets, and a standalone
    ``ax.text(...)`` annotation like ``create_pca_scree_plot``'s bar labels / seaborn's
    ``annot=True`` heatmap cells."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [1, 2, 3], label="series")
    ax.set_title("a title")
    ax.set_xlabel("x label")
    ax.set_ylabel("y label")
    ax.legend(title="Genotype")
    ax.text(0.5, 0.5, "an annotation")
    fig.suptitle("a suptitle")
    return fig


def _all_texts(fig):
    ax = fig.axes[0]
    texts = list(fig.texts)
    texts.extend([ax.title, ax.xaxis.label, ax.yaxis.label])
    texts.extend(ax.get_xticklabels())
    texts.extend(ax.get_yticklabels())
    texts.extend(ax.texts)
    legend = ax.get_legend()
    texts.extend(legend.get_texts())
    texts.append(legend.get_title())
    return texts


def test_apply_font_style_noop_when_both_none():
    apply_font_style(object(), font_family=None, font_size=None)  # no exception


def test_apply_font_style_sets_font_family_on_title_labels_ticks_and_legend():
    fig = _styled_figure()
    apply_font_style(fig, font_family="serif")
    for text in _all_texts(fig):
        assert text.get_fontfamily() == ["serif"], text.get_text()


def test_apply_font_style_sets_font_size():
    fig = _styled_figure()
    apply_font_style(fig, font_size=22)
    for text in _all_texts(fig):
        assert text.get_fontsize() == 22, text.get_text()


def test_apply_font_style_family_only_leaves_size_unchanged():
    fig = _styled_figure()
    default_size = fig.axes[0].title.get_fontsize()
    apply_font_style(fig, font_family="serif")
    assert fig.axes[0].title.get_fontsize() == default_size


def test_apply_font_style_size_only_leaves_family_unchanged():
    fig = _styled_figure()
    default_family = fig.axes[0].title.get_fontfamily()
    apply_font_style(fig, font_size=22)
    assert fig.axes[0].title.get_fontfamily() == default_family


def test_apply_font_style_skips_axes_without_a_legend():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.set_title("no legend here")
    apply_font_style(fig, font_family="serif")  # no exception
    assert ax.title.get_fontfamily() == ["serif"]


def test_apply_font_style_covers_legend_title_not_just_entries():
    """Regression: create_pca_biplot's ax.legend(title=color_by, ...) gives the legend a
    real title distinct from its entry labels — both must be styled, not just the entries.
    """
    fig = _styled_figure()
    legend = fig.axes[0].get_legend()
    apply_font_style(fig, font_family="serif", font_size=22)
    assert legend.get_title().get_fontfamily() == ["serif"]
    assert legend.get_title().get_fontsize() == 22


def test_apply_font_style_applies_to_every_axes_on_a_multi_axes_figure():
    """Regression: iterating fig.axes (not fig.gca()) must reach every Axes a figure
    carries — e.g. create_feature_contribution_heatmap's seaborn colorbar Axes."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(1, 2)
    ax1.set_title("first")
    ax2.set_title("second")
    apply_font_style(fig, font_family="serif")
    assert ax1.title.get_fontfamily() == ["serif"]
    assert ax2.title.get_fontfamily() == ["serif"]


def test_apply_font_style_covers_figure_level_suptitle():
    """Regression: create_umap_colored_by_top_traits sets its overall heading via
    fig.suptitle(title, fontsize=14) — a Text that lives on the Figure itself
    (fig.texts), not on any Axes. Iterating fig.axes alone never reaches it."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    suptitle = fig.suptitle("overall heading")
    apply_font_style(fig, font_family="serif", font_size=22)
    assert suptitle.get_fontfamily() == ["serif"]
    assert suptitle.get_fontsize() == 22


def test_apply_font_style_covers_standalone_annotation_text():
    """Regression: create_pca_biplot's per-arrow trait-name labels and
    create_pca_scree_plot's per-bar percentage annotations are both standalone
    ax.text(...) calls — not the title, an axis label, a tick label, or legend text —
    so they live in ax.texts and are otherwise invisible to apply_font_style."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    annotation = ax.text(0.5, 0.5, "PC1 (42%)")
    apply_font_style(fig, font_family="serif", font_size=22)
    assert annotation.get_fontfamily() == ["serif"]
    assert annotation.get_fontsize() == 22


def test_apply_font_style_covers_seaborn_annot_true_heatmap_cells():
    """Regression: create_feature_contribution_heatmap draws via
    sns.heatmap(..., annot=True) — verified directly that seaborn's cell-value text
    lands in ax.texts (the same standalone-annotation mechanism as the test above),
    not any of title/axis-label/tick-label/legend."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import seaborn as sns

    fig, ax = plt.subplots()
    sns.heatmap(np.array([[0.1, 0.2], [0.3, 0.4]]), annot=True, ax=ax)
    assert len(ax.texts) > 0  # sanity: seaborn really did add standalone cell text
    apply_font_style(fig, font_family="serif")
    for text in ax.texts:
        assert text.get_fontfamily() == ["serif"]


def test_generate_figures_forwards_font_kwargs_to_each_figure():
    figures: dict = {}
    generate_figures(
        {"a": _styled_figure, "b": _styled_figure},
        figures,
        font_family="serif",
        font_size=18,
    )
    for fig in figures.values():
        assert fig.axes[0].title.get_fontfamily() == ["serif"]
        assert fig.axes[0].title.get_fontsize() == 18


def test_generate_figures_records_figure_before_styling(monkeypatch):
    """Regression: if apply_font_style itself ever raised, the figure must already be
    recorded into the caller's dict (recorded before styling, not after) so
    close_figures can still reach it in finally — otherwise it would leak from
    matplotlib's Agg registry, unrecorded and unreachable."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from bloom_mcp.tools import _plots

    def _boom(fig, *, font_family=None, font_size=None):
        raise RuntimeError("styling blew up")

    monkeypatch.setattr(_plots, "apply_font_style", _boom)

    figures: dict = {}
    with pytest.raises(RuntimeError):
        generate_figures({"a": lambda: plt.figure()}, figures, font_family="serif")
    assert "a" in figures  # recorded before the (simulated) styling failure


# ── generate_figures: paginated (list[Figure]) plotter returns, bloom#462 ─────
#
# `sleap_roots_analyze.create_heritability_plot` returns a single Figure at or below
# its `traits_per_page` default (50 traits) and a `list[Figure]` above it. Before
# bloom#462 a list return would have been stored under one key and then handed to
# `plt.close(<list>)` by `close_figures`, leaking every page.


def test_generate_figures_expands_a_list_return_into_numbered_pages():
    figures: dict = {}
    generate_figures({"multi": lambda: ["p1", "p2", "p3"]}, figures)
    assert figures == {"multi_page1": "p1", "multi_page2": "p2", "multi_page3": "p3"}


def test_generate_figures_single_figure_key_naming_is_unchanged():
    """Regression guard for pca_analysis/umap_analysis/clustering: a scalar return
    must keep its bare key, with no `_page` suffix. Their persisted output keys
    (`<key>.png`) are a caller-visible contract this change must not move."""
    figures: dict = {}
    generate_figures({"a": lambda: "fig_a", "b": lambda: "fig_b"}, figures)
    assert figures == {"a": "fig_a", "b": "fig_b"}
    assert not any("_page" in k for k in figures)


def test_generate_figures_mixes_scalar_and_list_returns_in_one_call():
    figures: dict = {}
    generate_figures(
        {"solo": lambda: "fig_solo", "multi": lambda: ["p1", "p2"]}, figures
    )
    assert figures == {"solo": "fig_solo", "multi_page1": "p1", "multi_page2": "p2"}


def test_generate_figures_empty_list_return_records_no_phantom_entry():
    figures: dict = {}
    generate_figures({"multi": lambda: []}, figures)
    assert figures == {}


def test_generate_figures_expansion_is_isinstance_list_not_duck_typed():
    """A string is iterable. If the expansion probed `__iter__` instead of checking
    `isinstance(..., list)`, this module's own string-sentinel tests above would
    silently become one page per character."""
    figures: dict = {}
    generate_figures({"a": lambda: "fig_a"}, figures)
    assert figures == {"a": "fig_a"}


def test_generate_figures_closes_every_page_of_a_list_return():
    """End-to-end with real figures: close_figures must reach every page."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from bloom_mcp.tools._plots import close_figures

    plt.close("all")
    figures: dict = {}
    generate_figures({"multi": lambda: [plt.figure() for _ in range(3)]}, figures)
    assert len(figures) == 3
    close_figures(figures)
    assert plt.get_fignums() == []


def test_generate_figures_records_all_pages_before_styling_any(monkeypatch):
    """The two-pass shape, pinned: a styling failure on page 2 of a 3-page return must
    still leave pages 1-3 in the caller's dict for close_figures to reach in finally.

    An interleaved record->style->record loop would satisfy this only for pages 1-2:
    page 3 was already allocated by fn() (the whole list is produced in one call),
    live in matplotlib's registry, but never recorded and so unreachable."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from bloom_mcp.tools import _plots
    from bloom_mcp.tools._plots import close_figures

    calls = {"n": 0}

    def _boom_on_second(fig, *, font_family=None, font_size=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("styling blew up on page 2")

    monkeypatch.setattr(_plots, "apply_font_style", _boom_on_second)

    plt.close("all")
    figures: dict = {}
    with pytest.raises(RuntimeError):
        generate_figures(
            {"multi": lambda: [plt.figure() for _ in range(3)]},
            figures,
            font_family="serif",
        )
    assert set(figures) == {"multi_page1", "multi_page2", "multi_page3"}
    close_figures(figures)
    assert plt.get_fignums() == []


def test_generate_figures_closes_pages_a_plotter_built_then_abandoned():
    """No figure a call creates is left open on any exit path — both halves.

    Pages an earlier key *returned* are recorded in the caller's dict and reach
    `close_figures` in `finally`. Pages a later key *built and then abandoned by raising*
    never reach the caller, so recording cannot see them — `call_with_figure_cleanup`
    (#721, landed in #726) closes those under `FIGURE_REGISTRY_LOCK` before re-raising. A
    paginating plotter is exactly where this matters: it may build several pages before
    dying on a later one.

    An earlier version of this test asserted the abandoned pages DID leak (2), because the
    fignums-diff fix is only sound under a process-wide lock and #726 had not yet landed;
    it carried the instruction to flip to `== 0` once it did. This is that flip.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from bloom_mcp.tools._plots import close_figures

    plt.close("all")

    def _builds_two_pages_then_raises():
        plt.figure()
        plt.figure()
        raise RuntimeError("plotter died mid-pagination")

    figures: dict = {}
    with pytest.raises(RuntimeError):
        generate_figures(
            {
                "first": lambda: [plt.figure() for _ in range(3)],
                "second": _builds_two_pages_then_raises,
            },
            figures,
        )

    # Everything the first key returned is recorded and therefore closable.
    assert set(figures) == {"first_page1", "first_page2", "first_page3"}
    close_figures(figures)
    # The two the second key abandoned were closed by call_with_figure_cleanup on the way
    # out — nothing is left in matplotlib's registry.
    assert plt.get_fignums() == []


# ── FIGURE_REGISTRY_LOCK (#466 review round 6) ───────────────────────────────


def test_figure_registry_lock_is_a_real_process_wide_lock():
    """A minimal smoke test that the lock exists, is acquirable/releasable, and is
    genuinely process-wide (module-level singleton, not per-import) — the 3
    #466-converged viz tools and generate_figures's own allocate-then-raise cleanup
    (#726) all import and acquire this SAME object."""
    import threading

    assert isinstance(FIGURE_REGISTRY_LOCK, type(threading.Lock()))
    acquired = FIGURE_REGISTRY_LOCK.acquire(blocking=False)
    assert acquired
    FIGURE_REGISTRY_LOCK.release()


def test_close_figures_holds_the_registry_lock_while_closing(monkeypatch):
    """``close_figures`` closes under the lock, not just around creation.

    ``plt.close`` -> ``Gcf.destroy_fig`` scans the shared ``Gcf.figs`` dict to find
    the owning manager, so an unlocked close here can race a locked create in
    another thread and raise ``RuntimeError("OrderedDict mutated during iteration")``
    out of an unrelated caller (#466 review round 7).
    """
    import matplotlib.pyplot as plt

    real_close = plt.close
    held: list[bool] = []

    def _spy_close(fig):
        held.append(FIGURE_REGISTRY_LOCK.locked())
        return real_close(fig)

    monkeypatch.setattr(plt, "close", _spy_close)
    close_figures({"a": plt.figure(), "b": plt.figure()})
    assert held == [True, True]


def test_close_figures_releases_the_registry_lock_afterwards():
    """The lock must not be left held once cleanup returns — a leaked acquisition
    would deadlock the next figure-creating tool call in the process."""
    import matplotlib.pyplot as plt

    close_figures({"a": plt.figure()})
    assert not FIGURE_REGISTRY_LOCK.locked()
