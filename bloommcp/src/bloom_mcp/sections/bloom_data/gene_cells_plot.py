"""scrna_gene_cells' UMAP image: the arrays the call read, drawn and sent after the JSON."""

from __future__ import annotations

from typing import Any

import numpy as np

from bloom_mcp.contract import ToolImage
from bloom_mcp.data_access import expression_stats
from bloom_mcp.data_access.scrna_plots import _png, umap

from ._page import _cut


def gene_umap(
    cells: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    values: list[float],
    scope: set[int] | None,
    title: str,
    deadline: float,
) -> tuple[ToolImage | None, str | None]:
    """The UMAP coloured by `values`, or the reason there is none."""
    types = expression_stats.cell_types(clusters)
    level = {t.ordinal: i for i, t in enumerate(types)}
    codes = np.array([level.get(c.get("cluster_ordinal"), -1) for c in cells])
    focus = None
    if scope is not None:
        focus = np.zeros(len(cells), dtype=bool)
        focus[list(scope)] = True
    x = np.array([c["x"] for c in cells], dtype=float)
    y = np.array([c["y"] for c in cells], dtype=float)
    shown = _cut(title)[0]
    drawn = _png.draw(
        lambda: umap.gene_figure(
            x,
            y,
            np.asarray(values, dtype=float),
            codes,
            [t.name for t in types],
            focus,
            shown,
        ),
        deadline,
    )
    if drawn.png is None:
        return None, drawn.reason
    return ToolImage(drawn.png, "image/png", f"UMAP: {shown}"), None
