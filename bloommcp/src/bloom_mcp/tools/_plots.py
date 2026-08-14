"""Tool-agnostic plot helpers shared by PCA, UMAP, and future analysis tools.

Callers build a ``resolved_calls`` dict of zero-arg callables — one per plot
key — each wrapping a plotter call with its bespoke args.  This module then
validates, dispatches, and cleans up without knowing anything about the
caller's result type or the upstream plotter's API.

Follows the ``_qc_shared`` precedent: pure validation + dispatch logic,
importable with no live stack and no matplotlib import at module level.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # matplotlib stays out of the runtime import graph
    from matplotlib.figure import Figure

from bloom_mcp.contract import BloomMCPError


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
    """
    for key, fn in resolved_calls.items():
        figures[key] = fn()
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
