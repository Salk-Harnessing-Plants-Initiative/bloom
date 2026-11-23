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

NOWHERE = "No cell has a UMAP position, so there is no plot."
PLURAL = {"cell_type": "cell types", "genotype": "genotypes", "replicate": "replicates"}


@dataclass(frozen=True)
class _Map:
    x: np.ndarray
    y: np.ndarray
    # Each cell's index into `names`, -1 for no cell type.
    codes: np.ndarray
    names: list[str]
    colours: list[umap_groups.Rgb]
    focus: np.ndarray | None
    # Which of the call's cells have a UMAP position; only those are drawn.
    placed: np.ndarray


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
    x = np.array([c["x"] for c in cells], dtype=float)
    y = np.array([c["y"] for c in cells], dtype=float)
    placed = np.isfinite(x) & np.isfinite(y)
    codes = np.array([level.get(c.get("cluster_ordinal"), -1) for c in cells], int)
    return _Map(
        x=x[placed],
        y=y[placed],
        codes=codes[placed],
        names=[t.name for t in types],
        colours=[umap_groups.stored_colour(stored.get(t.ordinal)) for t in types],
        focus=None if focus is None else focus[placed],
        placed=placed,
    )


def _unplaced(m: _Map) -> list[str]:
    """A note on cells with no UMAP position, which are left off the map."""
    missing = int((~m.placed).sum())
    if missing == 0:
        return []
    if missing == 1:
        return ["1 cell has no UMAP position and is not drawn."]
    return [f"{missing:,} cells have no UMAP position and are not drawn."]


def _image(
    build: Callable[[], Figure], m: _Map, title: str, deadline: float
) -> tuple[ToolImage | None, list[str]]:
    if not m.placed.any():
        return None, [NOWHERE]
    drawn = _png.draw(build, deadline)
    if drawn.png is None:
        return None, _unplaced(m) + ([drawn.reason] if drawn.reason else [])
    return ToolImage(drawn.png, "image/png", f"UMAP: {title}"), _unplaced(m)


def gene_umap(
    cells: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    values: list[float],
    scope: set[int] | None,
    title: str,
    units: str,
    deadline: float,
) -> tuple[ToolImage | None, list[str]]:
    """The UMAP coloured by `values` in `units`, or notes saying why there is none."""
    m, shown = _map(cells, clusters, scope), _cut(title)[0]
    lit = np.asarray(values, dtype=float)[m.placed]
    return _image(
        lambda: umap.gene_figure(
            m.x, m.y, lit, m.codes, m.names, m.focus, shown, units=units
        ),
        m,
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
        groups = groups[m.placed]
        colours = umap_groups.palette(len(names))
        none, none_name = umap_groups.NO_VALUE, expression_stats.NOT_RECORDED
        if not names:
            notes.append(f"No cell records a {color_by}, so every cell is grey.")
    if len(names) > umap.MAX_GROUPS:
        return None, notes + [
            f"{len(names):,} {PLURAL[color_by]}, more than the {umap.MAX_GROUPS} one "
            "picture can colour, so there is no plot."
        ]
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
        m,
        shown,
        deadline,
    )
    return image, notes + reasons
