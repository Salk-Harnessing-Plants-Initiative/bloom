"""A dataset's UMAP coloured by one gene, drawn as the website's UMAP tab draws it
(expression-umap.tsx, expression-lib/shaders.ts): dark ground, viridis from the gene's lowest
to highest value over every cell, and cells at zero under the expressing cells. Cells outside
the named cell types are drawn first in faint grey; the website hides them instead."""

from __future__ import annotations

import math

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
LABEL_FONT, LEGEND_FONT = 9, 8
# A group UMAP's map ends here (a fraction of the width); its legend fills the rest.
MAP_RIGHT = 0.58
_GROUP_MAP = (0.02, 0.04, MAP_RIGHT - 0.02, 0.88)
# Most groups one picture colours, the legend rows per column and the longest name shown
# in one column (half that in two), so that the legend fits inside the image.
MAX_GROUPS = 50
LEGEND_ROWS = 26
LEGEND_CHARS = 40


def _axes(
    title: str, rect: tuple[float, float, float, float] | None = None
) -> tuple[Figure, Axes]:
    fig = _png.new_figure()
    fig.set_facecolor(GROUND)
    if rect is None:
        ax = fig.add_subplot(label=MAP_AXES)
    else:
        ax = fig.add_axes(rect, label=MAP_AXES)
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
            ax_x,
            ax_y,
            names[level],
            color=TEXT,
            ha="center",
            va="center",
            fontsize=LABEL_FONT,
        )


def _short(name: str, limit: int) -> str:
    """`name` cut in the middle to `limit` characters, keeping its start and end."""
    if len(name) <= limit:
        return name
    head = (limit - 1) // 2
    return name[:head] + "…" + name[len(name) - (limit - 1 - head) :]


def gene_figure(
    x: np.ndarray,
    y: np.ndarray,
    values: np.ndarray,
    codes: np.ndarray,
    names: list[str],
    focus: np.ndarray | None,
    title: str,
    *,
    units: str,
) -> Figure:
    """The UMAP coloured by `values`, in `units`; `codes` index `names` (-1: no cell type);
    `focus` marks the cells of the named cell types (None: every cell)."""
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
    bar.set_label(units, color=TEXT)
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
    written on the map from `label_codes`. The legend fits beside the map for up to
    MAX_GROUPS groups."""
    fig, ax = _axes(title, _GROUP_MAP)
    lit = np.ones(len(x), dtype=bool) if focus is None else focus
    if focus is not None:
        _points(ax, x[~lit], y[~lit], [FOCUS_GREY], OUTSIDE)
    table = np.array([(*c, 1.0) for c in colours] + [tuple(none_colour)])
    _points(ax, x[lit], y[lit], table[groups[lit]], GROUPS)
    entries = [(name, (*colour, 1.0)) for name, colour in zip(group_names, colours)]
    if none_name and (groups == -1).any():
        entries.append((none_name, tuple(none_colour)))
    columns = max(1, math.ceil(len(entries) / LEGEND_ROWS))
    handles = [
        Line2D(
            [],
            [],
            marker="o",
            linestyle="",
            color=c,
            label=_short(n, LEGEND_CHARS // columns),
        )
        for n, c in entries
    ]
    legend = ax.legend(
        handles=handles,
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        frameon=False,
        fontsize=LEGEND_FONT,
        ncol=columns,
        handlelength=1.0,
        handletextpad=0.4,
        columnspacing=1.0,
    )
    for text in legend.get_texts():
        text.set_color(TEXT)
    _labels(ax, x, y, label_codes, label_names, focus)
    return fig
