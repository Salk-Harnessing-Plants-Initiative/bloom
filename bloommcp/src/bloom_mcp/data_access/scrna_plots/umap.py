"""A dataset's UMAP coloured by one gene, drawn as the website's UMAP tab draws it
(expression-umap.tsx, expression-lib/shaders.ts): dark ground, viridis from the gene's lowest
to highest value over every cell, cells at zero under the expressing cells, and cells outside
the named cell types first in faint grey."""

from __future__ import annotations

import numpy as np
from matplotlib import colormaps
from matplotlib.axes import Axes
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from . import _png, umap_labels

GROUND = (0.05, 0.05, 0.08)
FOCUS_GREY = (0.42, 0.42, 0.45, 0.35)
TEXT = (0.9, 0.9, 0.92)
# The website's 4 px points, in matplotlib's points squared.
POINT_SIZE = (4 * 72 / _png.DPI) ** 2
MAP_AXES = "umap"
OUTSIDE, ZERO, EXPRESSING, GROUPS = "outside", "zero", "expressing", "groups"
VIRIDIS = colormaps["viridis"]


def _axes(title: str) -> tuple[Figure, Axes]:
    fig = _png.new_figure()
    fig.set_facecolor(GROUND)
    ax = fig.add_subplot(label=MAP_AXES)
    ax.set_facecolor(GROUND)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title(title, color=TEXT)
    return fig, ax


def _points(ax: Axes, x: np.ndarray, y: np.ndarray, colours, gid: str) -> None:
    if len(x):
        ax.scatter(x, y, s=POINT_SIZE, c=colours, linewidths=0, gid=gid)


def _labels(
    ax: Axes,
    x: np.ndarray,
    y: np.ndarray,
    codes: np.ndarray,
    names: list[str],
    shown: np.ndarray | None,
) -> None:
    for level, (ax_x, ax_y) in umap_labels.anchors(
        x, y, codes, len(names), shown
    ).items():
        ax.text(
            ax_x, ax_y, names[level], color=TEXT, ha="center", va="center", fontsize=9
        )


def gene_figure(
    x: np.ndarray,
    y: np.ndarray,
    values: np.ndarray,
    codes: np.ndarray,
    names: list[str],
    focus: np.ndarray | None,
    title: str,
) -> Figure:
    """The UMAP coloured by `values`; `codes` index `names` (-1: no cell type); `focus`
    marks the cells of the named cell types (None: every cell)."""
    fig, ax = _axes(title)
    low, high = float(values.min()), float(values.max())
    norm = Normalize(low, high if high > low else low + 1e-6, clip=True)
    lit = np.ones(len(x), dtype=bool) if focus is None else focus
    if focus is not None:
        out = ~lit
        _points(ax, x[out], y[out], [FOCUS_GREY], OUTSIDE)
    for gid, part in ((ZERO, lit & ~(values > 0)), (EXPRESSING, lit & (values > 0))):
        _points(ax, x[part], y[part], VIRIDIS(norm(values[part])), gid)
    bar = fig.colorbar(ScalarMappable(norm, VIRIDIS), ax=ax, fraction=0.03)
    bar.ax.tick_params(colors=TEXT)
    bar.outline.set_visible(False)
    _labels(ax, x, y, codes, names, focus)
    return fig


def group_figure(
    x: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    colours: list[tuple[float, float, float]],
    none_colour: tuple[float, ...],
    group_names: list[str],
    label_codes: np.ndarray,
    label_names: list[str],
    focus: np.ndarray | None,
    title: str,
    *,
    none_name: str | None = None,
) -> Figure:
    """The UMAP with each cell in its group's colour, in stored cell order, with a legend;
    `groups` index `colours` and `group_names` (-1: `none_colour`). Cell-type names are
    written on the map from `label_codes`."""
    fig, ax = _axes(title)
    lit = np.ones(len(x), dtype=bool) if focus is None else focus
    if focus is not None:
        _points(ax, x[~lit], y[~lit], [FOCUS_GREY], OUTSIDE)
    table = np.array([(*c, 1.0) for c in colours] + [tuple(none_colour)])
    _points(ax, x[lit], y[lit], table[groups[lit]], GROUPS)
    entries = [(name, (*colour, 1.0)) for name, colour in zip(group_names, colours)]
    if none_name and (groups == -1).any():
        entries.append((none_name, tuple(none_colour)))
    handles = [
        Line2D([], [], marker="o", linestyle="", color=c, label=n) for n, c in entries
    ]
    legend = ax.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        frameon=False,
        fontsize=8,
        ncol=max(1, len(handles) // 30 + 1),
    )
    for text in legend.get_texts():
        text.set_color(TEXT)
    _labels(ax, x, y, label_codes, label_names, focus)
    return fig
