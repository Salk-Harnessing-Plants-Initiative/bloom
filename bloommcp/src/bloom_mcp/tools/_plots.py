"""Tool-agnostic plot helpers shared by PCA, UMAP, and future analysis tools.

Callers build a ``resolved_calls`` dict of zero-arg callables — one per plot
key — each wrapping a plotter call with its bespoke args.  This module then
validates, dispatches, and cleans up without knowing anything about the
caller's result type or the upstream plotter's API.

Follows the ``_qc_shared`` precedent: pure validation + dispatch logic,
importable with no live stack and no matplotlib import at module level.
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # matplotlib stays out of the runtime import graph
    from matplotlib.figure import Figure

from bloom_mcp.contract import BloomMCPError

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


# Process-wide, not per-key: matplotlib's pyplot figure registry (`Gcf.figs`, in
# `matplotlib._pylab_helpers`) is a single class-level `OrderedDict` shared by the whole
# process, not scoped per thread. FastMCP dispatches sync tool handlers via a thread pool
# (see `bloom_mcp/result_store/_locks.py`'s module docstring for the same fact, verified
# there against FastMCP's own dispatch code), so two `umap_analysis`/`pca_analysis` calls
# can genuinely run concurrently in separate threads of one process — both reaching
# `generate_figures` at the same time. Without this lock, the allocate-then-raise cleanup
# below (which detects "new since I started" purely by diffing the shared global
# `plt.get_fignums()`) cannot tell its own orphaned figure apart from one a *different*,
# unrelated concurrent call just allocated — and would close that other call's figure
# instead, silently corrupting or blanking its plot with no error surfaced to it at all
# (#721 PR review). Serializing the entire generate-then-cleanup window process-wide is
# the only fix available from within `bloommcp`: the plotters that actually call
# `plt.subplots()`/`plt.figure()` live in the vendored, third-party `sleap_roots_analyze`
# package, so switching them to construct `matplotlib.figure.Figure()` directly (bypassing
# the shared registry entirely) is not a change bloommcp can make unilaterally.
_FIGURE_REGISTRY_LOCK = threading.Lock()


def generate_figures(
    resolved_calls: dict[str, "Callable[[], Figure]"],
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

    ``font_family``/``font_size`` (both default ``None``) are applied via
    ``apply_font_style`` to each figure immediately after it is recorded into
    ``figures`` — a no-op when both are ``None``. Recording happens *before* styling
    (not after) so that if ``apply_font_style`` itself ever raised, the figure would
    already be in ``figures`` for ``close_figures`` to reach in ``finally``, rather than
    leaking from matplotlib's registry unrecorded and unreachable.

    A callable that internally allocates a figure (e.g. via ``plt.subplots()``) and then
    raises *before returning it* (#721 — e.g. an invalid colormap name reaching
    matplotlib deep inside the call) would otherwise leak that figure forever: the
    assignment ``figures[key] = fn()`` never completes, so it's never recorded, and
    ``close_figures`` only iterates the caller's dict. Guarded here by diffing
    ``plt.get_fignums()`` around each call and closing anything new before re-raising —
    under ``_FIGURE_REGISTRY_LOCK`` for the whole call, since that diff is only safe
    against a global registry no other call is concurrently mutating (see the lock's own
    comment).
    """
    import matplotlib.pyplot as plt

    with _FIGURE_REGISTRY_LOCK:
        for key, fn in resolved_calls.items():
            before = plt.get_fignums()
            try:
                figures[key] = fn()
            except Exception:
                for num in plt.get_fignums():
                    if num not in before:
                        plt.close(num)
                raise
            apply_font_style(figures[key], font_family=font_family, font_size=font_size)


def close_figures(figures: "dict[str, Figure]") -> None:
    """Close every figure in best-effort; never raises.

    Returns immediately on an empty dict to avoid importing matplotlib on the
    default no-plots path (Tier-0 import-clean guarantee).
    """
    if not figures:
        return
    try:
        import matplotlib.pyplot as plt

        for fig in figures.values():
            try:
                plt.close(fig)
            except Exception:  # pragma: no cover — best-effort cleanup
                pass
    except Exception:  # pragma: no cover — best-effort cleanup
        pass
