"""Tool-agnostic plot helpers shared by PCA, UMAP, and future analysis tools.

Callers build a ``resolved_calls`` dict of zero-arg callables — one per plot
key — each wrapping a plotter call with its bespoke args.  This module then
validates, dispatches, and cleans up without knowing anything about the
caller's result type or the upstream plotter's API.

Follows the ``_qc_shared`` precedent: pure validation + dispatch logic,
importable with no live stack and no matplotlib import at module level.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import TYPE_CHECKING, Callable, TypeVar

if TYPE_CHECKING:  # matplotlib stays out of the runtime import graph
    from matplotlib.figure import Figure

from bloom_mcp.contract import BloomMCPError

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# Sanity ceilings for plot style fields shared by UMAP and PCA (#721). Single-sourced here
# (not duplicated per tool file) so a future change to one doesn't silently desync the two —
# both `umap_analysis.py` and `pca_analysis.py` import from here rather than declaring their
# own copy. Values in the low thousands have been observed costing several seconds and
# multiple GB per render on this LLM-driven input surface; both ceilings are generous
# headroom over real use (fonts are almost always 6-72pt; scatter markers are almost always
# 1-500) while catching a runaway or adversarial request.
MAX_PLOT_FONT_SIZE = 100
MAX_PLOT_POINT_SIZE = 10000


def validate_plot_keys(requested: list[str] | None, valid_keys: set[str]) -> None:
    """Validate ``plots`` against the caller's catalog before any run is committed.

    - ``None`` → accepted (generate all keys; no validation needed).
    - ``[]`` → ``invalid_input`` (ambiguous: use ``None`` for all, or omit
      ``include_plots`` for none).
    - Unknown key → ``invalid_input`` naming the offending key(s).
    - Duplicate key → ``invalid_input`` naming the duplicate(s).
    """
    if requested is None:
        return
    if not requested:
        raise BloomMCPError(
            code="invalid_input",
            message="plots must be a non-empty list of plot keys, or None to generate all.",
            remedy=(
                "Pass at least one valid plot key, or omit plots (None) to generate every "
                "available plot."
            ),
        )
    unknown = [k for k in requested if k not in valid_keys]
    if unknown:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"plots names unknown figure key(s): {unknown}. "
                f"Available: {sorted(valid_keys)}."
            ),
            remedy="Use one of the available plot keys, or omit plots to generate all.",
        )
    counts = Counter(requested)
    dupes = [k for k, n in counts.items() if n > 1]
    if dupes:
        raise BloomMCPError(
            code="invalid_input",
            message=f"plots contains duplicate key(s): {dupes}.",
            remedy="Remove duplicate plot keys from the list.",
        )


def apply_font_style(
    fig: "Figure",
    *,
    font_family: str | None = None,
    font_size: float | None = None,
) -> None:
    """Override the font family/size of every text element on ``fig``.

    Covers figure-level text (``fig.texts``, which includes ``fig.suptitle(...)`` — a
    plain ``Text`` matplotlib records there, not on any ``Axes``) and, for every ``Axes``
    in ``fig.axes``: its title, x-axis label, y-axis label, tick labels, standalone
    annotation text (``ax.texts`` — where both freestanding ``ax.text(...)`` calls and
    seaborn's ``annot=True`` heatmap cell values live), and — when a legend is present —
    every legend entry and the legend's own title. A no-op that touches no attribute of
    ``fig`` when both ``font_family`` and ``font_size`` are ``None`` (the default), so
    passing a non-``Figure`` object is safe as long as neither override is requested.
    """
    if font_family is None and font_size is None:
        return
    texts = list(fig.texts)
    for ax in fig.axes:
        texts.append(ax.title)
        texts.append(ax.xaxis.label)
        texts.append(ax.yaxis.label)
        texts.extend(ax.get_xticklabels())
        texts.extend(ax.get_yticklabels())
        texts.extend(ax.texts)
        legend = ax.get_legend()
        if legend is not None:
            texts.extend(legend.get_texts())
            texts.append(legend.get_title())
    for text in texts:
        if font_family is not None:
            text.set_fontfamily(font_family)
        if font_size is not None:
            text.set_fontsize(font_size)


def check_plot_style_ceiling(
    value: float | None, *, field_name: str, max_value: float
) -> None:
    """Raise ``invalid_input`` if ``value`` is set and outside ``(0, max_value]``.

    Deliberately a plain range check in the tool body, not a Pydantic ``Field(gt=0,
    le=max_value)`` constraint (#721): a ``Field`` constraint's violation is caught by
    ``BloomMCPError.from_input_validation``, which surfaces only the field name + error
    type — never the submitted value or the ceiling — producing exactly the opaque
    message this PR eliminated for ``plot_cmap`` by moving that check into the tool body
    too. Calling this from each tool's own body, before any Pydantic constraint would
    apply, means the message can name both.

    NaN-safe: ``not (0 < nan <= max_value)`` is ``True`` (every comparison with ``nan`` is
    ``False``, so the chain short-circuits to ``False`` and ``not False`` is ``True``),
    matching the NaN-rejection Pydantic's own ``gt``/``le`` constraints provide — this
    check must not silently regress that guarantee just because it moved out of Pydantic.
    """
    if value is not None and not (0 < value <= max_value):
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"{field_name}={value!r} must be greater than 0 and at most "
                f"{max_value}."
            ),
            remedy=f"Use a {field_name} between 0 (exclusive) and {max_value} "
            f"(inclusive).",
        )


# Process-wide, not per-key, and NOT bloommcp.tools._plots-private: matplotlib's pyplot
# figure registry (`Gcf.figs`, in `matplotlib._pylab_helpers`) is a single class-level
# `OrderedDict` shared by the whole process, not scoped per thread. FastMCP dispatches sync
# tool handlers via a thread pool (see `bloom_mcp/result_store/_locks.py`'s module
# docstring for the same fact, verified there against FastMCP's own dispatch code), so any
# two figure-creating tool calls in this process — not just two `umap_analysis`/
# `pca_analysis` calls — can genuinely run concurrently. Without this lock,
# `call_with_figure_cleanup`'s allocate-then-raise cleanup below (which detects "new
# since I started" purely by diffing the shared global `plt.get_fignums()`) cannot tell
# its own orphaned figure apart from one a *different*, unrelated concurrent call just
# allocated — and would close that other call's figure instead, silently corrupting or
# blanking its plot with no error surfaced to it at all (#721 PR review).
#
# This is why every matplotlib-figure-creating call site in bloommcp goes through
# `call_with_figure_cleanup` rather than acquiring this lock ad hoc. Two shapes:
#   - Direct callers, wrapping their own figure-creating delegate call:
#     `qc_inspect.py`'s `_render_report`, `remove_outliers.py`'s `_make_figures`, and
#     each of the 3 `plot_*` tools #466 converged onto `@as_mcp_tool`
#     (`plot_trait_histograms.py`, `plot_trait_boxplots.py`,
#     `plot_correlation_matrix.py`).
#   - Via `generate_figures`, which calls it once per plot key: `pca_analysis.py`,
#     `umap_analysis.py`, `clustering.py`, and `heritability_analysis.py` (#462, which
#     retired the last 2 bare-`mcp.tool()` plot tools).
# Those two lists are exhaustive; keep them that way when adding a figure-creating tool.
#
# Scoping the lock to just that one call (not the caller's full
# save/commit/persist span) is sufficient: the diff can only ever be confused by a figure
# that is *created* while the lock is held, and the lock is a mutex — no other call's
# creation step can execute concurrently, regardless of how long the holder then takes to
# save/close/commit *after* creating.
#
# Sufficient for THAT hazard — but creation is only half of the contract. There is a
# second, independent race the create-side lock does not cover: `plt.close(fig)` ->
# `Gcf.destroy_fig` first *scans* `Gcf.figs.values()` to find the manager owning the
# figure, and that scan is unsynchronized. A locked create (`Gcf.set_active` does
# `figs[num] = manager` then `move_to_end`) mutating the dict mid-scan raises
# `RuntimeError("OrderedDict mutated during iteration")` out of the *closing* caller —
# reproduced deterministically on PR #683 (#466 review round 7, which caught round 6
# shipping a create-only half-fix). So every call site must hold this lock around
# `plt.close` too, not just around creation. Where that stands:
#   - `call_with_figure_cleanup`'s own exception-path close: inside its `with` (done).
#   - `plot_trait_histograms.py`/`plot_trait_boxplots.py`/`plot_correlation_matrix.py`:
#     create via `call_with_figure_cleanup`, success-path close under a second, separate
#     acquisition in `finally` (done, #466) — separate so `savefig`/commit I/O never runs
#     on a process-wide lock.
#   - `close_figures` below: one acquisition around the batch (done, #466), and the
#     close path for every `dict`-holding tool — `pca_analysis.py`, `umap_analysis.py`,
#     `clustering.py`, `heritability_analysis.py`, and `qc_inspect.py`'s
#     `_render_report` + `remove_outliers.py` (both done, #808; the latter's private
#     lock-free `_close_figure` helper was deleted in favour of this).
# The close side is now covered everywhere in `bloom_mcp`, so this lock is sufficient
# for the race and no longer merely a precondition for closing it (#808). A third item
# used to sit in this list — `_viz_shared.py`'s `save_plot` — but #462 deleted that
# helper along with its only two callers rather than wiring it, so there is nothing
# left there to lock. `tests/tools/test_plots_helpers.py` pins both halves: that this
# comment claims no outstanding site, and (by AST walk) that no `plt.close` call site
# in `bloom_mcp` sits outside a `with FIGURE_REGISTRY_LOCK:` block.
#
# Non-reentrant: a future plotter that transitively re-enters `call_with_figure_cleanup`
# (or any other lock-acquiring call) from inside its own locked call would deadlock.
# Nothing in this codebase does that today.
#
# The alternative fix (have every plotter construct `matplotlib.figure.Figure()` directly,
# bypassing the shared registry entirely) isn't available from within `bloommcp`: every
# call site above delegates its actual rendering to the vendored, third-party
# `sleap_roots_analyze` package.
FIGURE_REGISTRY_LOCK = threading.Lock()


def call_with_figure_cleanup(fn: "Callable[[], _T]") -> "_T":
    """Call ``fn`` under ``FIGURE_REGISTRY_LOCK``; on exception, close any figure(s)
    newly registered in matplotlib's global registry since before the call, then
    re-raise. On success, returns ``fn()``'s result unchanged.

    The single, shared implementation of "safely create a figure" for every
    matplotlib-figure-creating call site in `bloommcp` (see `FIGURE_REGISTRY_LOCK`'s own
    comment for the full list) — not just `generate_figures`. Before this helper existed,
    the other call sites' own ``except Exception: return "<message>"`` blocks swallowed
    the exception without closing whatever the delegate had already allocated mid-render
    (a real, pre-existing leak at any of them whose delegate can raise *after* partially
    rendering — not merely a documentation gap, #721 PR review round 4): calling this
    instead of a bare delegate call closes that gap for all of them at once, for the same
    reason it was already necessary inside `generate_figures`.

    ``fn`` may allocate zero, one, or many figures (batched plotters return
    ``list[Figure]``) before returning or raising — this helper doesn't care what ``fn``
    returns, only what new figure numbers appear in the global registry while it runs.
    """
    import matplotlib.pyplot as plt

    with FIGURE_REGISTRY_LOCK:
        before = plt.get_fignums()
        try:
            return fn()
        except Exception:
            for num in plt.get_fignums():
                if num not in before:
                    plt.close(num)
            raise


def generate_figures(
    resolved_calls: dict[str, "Callable[[], Figure | list[Figure]]"],
    figures: "dict[str, Figure]",
    *,
    font_family: str | None = None,
    font_size: float | None = None,
) -> None:
    """Call each zero-arg plotter callable, recording each result into ``figures``.

    Populates ``figures`` one key at a time — not via an all-or-nothing dict
    comprehension — so a mid-generation exception still leaves every
    already-successful figure in the caller's dict for ``close_figures`` to
    reach in ``finally``. The caller passes the same dict it later closes.

    **A plotter may return a single ``Figure`` or a ``list[Figure]``.** A list is
    expanded into one ``<key>_page<N>`` entry per figure (1-indexed); a single figure
    keeps its bare ``<key>``, byte-identical to this function's pre-pagination
    behavior, so ``pca_analysis``/``umap_analysis``/``clustering`` output keys are
    unaffected. Expanding here (rather than storing the list under one key) is what
    lets ``apply_font_style`` and ``close_figures`` keep operating on a flat
    ``dict[str, Figure]`` with no special case of their own.

    The motivating case is ``sleap_roots_analyze.create_heritability_plot``, which
    returns a single figure at or below its ``traits_per_page`` default (50 traits) and
    a paginated list above it — cylinder's ~846 traits reach it. This mirrors the
    shape the legacy plotting tools' ``_viz_shared.save_plot_or_plots`` used for
    multi-page output before #462/#466 retired the last callers of that helper.

    Detection is a strict ``isinstance(..., list)`` check, deliberately not a
    duck-typed ``__iter__`` probe: this module's own tests pass string sentinels
    (``lambda: "fig_a"``), which an iterable check would silently shred into
    one page per character.

    **Cleanup guarantee.** Everything this function *records* is reachable by the
    caller's ``close_figures`` in ``finally``; everything a plotter allocates and then
    abandons by raising before returning is closed by ``call_with_figure_cleanup`` (see
    below). Between the two, no figure a call creates is left open on any exit path — the
    gap an earlier draft of this docstring documented (and ``test_plots_helpers.py`` pinned
    as a known leak, pending #721) is closed now that #726 landed.

    ``font_family``/``font_size`` (both default ``None``) are applied via
    ``apply_font_style`` to each recorded figure — a no-op when both are ``None``.
    **Every page of a call is recorded into ``figures`` before any page of that call is
    styled.** Recording before styling is what makes a raising ``apply_font_style``
    survivable at all (the figure is already reachable by the caller's
    ``close_figures`` in ``finally``); doing it per-page in an interleaved
    record→style→record loop would honor that only up to the failing page, and would
    strand every later page of the same list — already returned by ``fn()``, live in
    matplotlib's registry, never recorded, unreachable. Hence the two-pass shape below.

    Each call is made via ``call_with_figure_cleanup`` (#721), which acquires
    ``FIGURE_REGISTRY_LOCK`` for the duration of that one call and, on exception, closes
    any figure the callable allocated and then abandoned by raising *before returning*.
    For a paginating plotter that is exactly the "built pages 1..k, died on page k+1"
    case: the k finished pages never reached the caller, so the two-pass recording above
    cannot see them — the per-call cleanup is what closes them. The lock is held per key,
    not for the whole loop: it is a mutex, so narrowing to per-key doesn't reopen the race
    it exists to close, and it minimizes how long any one ``generate_figures`` invocation
    blocks every other concurrent figure-creating call in the process.
    """
    for key, fn in resolved_calls.items():
        result = call_with_figure_cleanup(fn)
        if isinstance(result, list):
            page_keys = [f"{key}_page{i}" for i in range(1, len(result) + 1)]
            for page_key, fig in zip(page_keys, result):
                figures[page_key] = fig
        else:
            page_keys = [key]
            figures[key] = result
        for page_key in page_keys:
            apply_font_style(
                figures[page_key], font_family=font_family, font_size=font_size
            )


def close_figures(figures: "dict[str, Figure]") -> None:
    """Close every figure in best-effort; never raises.

    Returns immediately on an empty dict to avoid importing matplotlib on the
    default no-plots path (Tier-0 import-clean guarantee).

    Holds ``FIGURE_REGISTRY_LOCK`` across the closes: ``plt.close`` scans the
    shared ``Gcf.figs`` registry, so an unlocked close here could race a locked
    create elsewhere in the process (see that lock's comment above). Acquired
    once around the whole batch rather than per figure — the lock is
    non-reentrant and nothing under it re-enters, and one acquisition keeps a
    multi-figure cleanup from interleaving with a create halfway through.

    Since #808 this is the close path for every ``dict``-holding tool:
    ``pca_analysis``, ``umap_analysis``, ``clustering``, ``heritability_analysis``,
    and — newly — ``qc_inspect`` and ``remove_outliers``.

    **Best-effort but not silent.** A failure closing one figure neither aborts the
    batch (which would strand the rest) nor propagates (which, called from a
    ``finally``, would replace whatever exception was already in flight) — but it is
    logged at ``WARNING`` naming the key. That log line matters more than it looks:
    no close site in ``bloom_mcp`` raises any more, so it is the only remaining
    signal that a registry race is still occurring, and a swallowed close means a
    figure leaked in a long-lived server process.
    """
    if not figures:
        return
    try:
        import matplotlib.pyplot as plt

        with FIGURE_REGISTRY_LOCK:
            for key, fig in figures.items():
                try:
                    plt.close(fig)
                except Exception as exc:
                    logger.warning(
                        "close_figures: plt.close failed for %r (figure leaked): %r",
                        key,
                        exc,
                    )
    except Exception as exc:  # pragma: no cover — best-effort cleanup
        logger.warning(
            "close_figures: could not close %d figure(s) (all leaked): %r",
            len(figures),
            exc,
        )
