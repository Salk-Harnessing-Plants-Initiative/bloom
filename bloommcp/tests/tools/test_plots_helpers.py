"""Unit tests for the tool-agnostic ``_plots`` helpers.

These tests exercise ``validate_plot_keys``, ``generate_figures``, and
``close_figures`` in isolation — no live stack, no Supabase, no matplotlib
import on the validation path.
"""

from __future__ import annotations

import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.tools._plots import (
    apply_font_style,
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


def test_generate_figures_records_pages_from_earlier_keys_when_a_later_key_raises():
    """Where the cleanup guarantee holds, and where it stops.

    HOLDS: every page of an earlier, successfully-returned key stays in the caller's dict,
    so `close_figures` reaches all of them in `finally` — the multi-page analogue of the
    single-figure partial-failure test above.

    DOES NOT HOLD: figures a plotter allocates internally and then abandons by raising
    *before returning* are never recorded, so they leak. That is asserted here as current
    behaviour rather than left silent, because a paginating plotter widens the window (it
    may build several pages before failing on a later one). Fixing it needs a
    `plt.get_fignums()` diff around the call, which is only sound under a process-wide lock
    — matplotlib's registry is shared and FastMCP dispatches sync handlers on a thread
    pool, so an unsynchronised diff would close other calls' figures. bloom#721 / PR #726
    adds the diff and the lock together; this test should be updated to assert no leak once
    that lands.
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

    # The two the second key abandoned are the documented gap: unrecorded, still open.
    leaked = plt.get_fignums()
    assert len(leaked) == 2, (
        "expected the 2 abandoned pages to leak; if this now passes with 0, PR #726's "
        "fignums diff has landed — tighten this assertion to `== 0` and update the "
        "docstring in generate_figures accordingly"
    )
    plt.close("all")
