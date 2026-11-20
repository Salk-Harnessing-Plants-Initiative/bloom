"""scrna_gene_cells' UMAP images: the arrays the call read, drawn and sent after the JSON."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from matplotlib.figure import Figure

from bloom_mcp.contract import ToolImage
from bloom_mcp.data_access import expression_stats
from bloom_mcp.data_access.scrna_plots import _png, umap, umap_groups

from ._page import _cut


@dataclass(frozen=True)
class _Map:
    x: np.ndarray
    y: np.ndarray
    # Each cell's index into `names`, -1 for no cell type.
    codes: np.ndarray
    names: list[str]
    colours: list[umap_groups.Rgb]
    focus: np.ndarray | None


def _map(
    cells: list[dict[str, Any]], clusters: list[dict[str, Any]], scope: set[int] | None
) -> _Map:
    types = expression_stats.cell_types(clusters)
    level = {t.ordinal: i for i, t in enumerate(types)}
    stored = {c["ordinal"]: c.get("color") for c in clusters}
    focus = None
    if scope is not None:
        focus = np.zeros(len(cells), dtype=bool)
        focus[list(scope)] = True
    return _Map(
        x=np.array([c["x"] for c in cells], dtype=float),
        y=np.array([c["y"] for c in cells], dtype=float),
        codes=np.array([level.get(c.get("cluster_ordinal"), -1) for c in cells]),
        names=[t.name for t in types],
        colours=[umap_groups.stored_colour(stored.get(t.ordinal)) for t in types],
        focus=focus,
    )


def _image(
    build: Callable[[], Figure], title: str, deadline: float
) -> tuple[ToolImage | None, list[str]]:
    drawn = _png.draw(build, deadline)
    if drawn.png is None:
        return None, [drawn.reason] if drawn.reason else []
    return ToolImage(drawn.png, "image/png", f"UMAP: {title}"), []


def gene_umap(
    cells: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    values: list[float],
    scope: set[int] | None,
    title: str,
    deadline: float,
) -> tuple[ToolImage | None, list[str]]:
    """The UMAP coloured by `values`, or notes saying why there is none."""
    m, shown = _map(cells, clusters, scope), _cut(title)[0]
    lit = np.asarray(values, dtype=float)
    return _image(
        lambda: umap.gene_figure(m.x, m.y, lit, m.codes, m.names, m.focus, shown),
        shown,
        deadline,
    )


def group_umap(
    cells: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    scope: set[int] | None,
    color_by: str,
    title: str,
    deadline: float,
) -> tuple[ToolImage | None, list[str]]:
    """The UMAP coloured by cell type, genotype or replicate, and any notes."""
    m, shown = _map(cells, clusters, scope), _cut(title)[0]
    notes: list[str] = []
    if color_by == "cell_type":
        groups, names, colours = m.codes, m.names, m.colours
        none, none_name = (*umap_groups.NO_CELL_TYPE_COLOUR, 1.0), "No cell type"
    else:
        groups, names = umap_groups.codes([c.get(color_by) for c in cells])
        colours = umap_groups.palette(len(names))
        none, none_name = umap_groups.NO_VALUE, expression_stats.NOT_RECORDED
        if not names:
            notes.append(f"No cell records a {color_by}, so every cell is grey.")
    image, reasons = _image(
        lambda: umap.group_figure(
            m.x,
            m.y,
            groups,
            colours,
            none,
            [_cut(n)[0] for n in names],
            m.codes,
            m.names,
            m.focus,
            shown,
            none_name=none_name,
        ),
        shown,
        deadline,
    )
    return image, notes + reasons
