"""A gene's expression per cell type, as the website's Genes by cell type view computes it
(web/components/expression-lib/gene-stats.ts). Every cell of a group counts, zeros included:
a cell the gene's file does not name has the value 0."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

NO_CELL_TYPE = "No cell type"
NOT_RECORDED = "Not recorded"
# The cell facet holding transgene status, and its positive value (transgene.ts).
TRANSGENE_FACET = "transgene_pos"
TRANSGENE_POSITIVE = "True"

Split = Literal["none", "genotype", "transgene"]


@dataclass(frozen=True)
class CellType:
    ordinal: int
    name: str


@dataclass(frozen=True)
class CellGroup:
    cell_type: str
    # The genotype or transgene status, or None when not split.
    part: str | None
    # Indexes into the dataset's cells.
    cells: list[int]


@dataclass(frozen=True)
class GroupStats:
    cells: int
    expressing: int
    share: float
    mean: float
    min: float
    q1: float
    median: float
    q3: float
    max: float


def cell_types(clusters: Sequence[dict[str, Any]]) -> list[CellType]:
    """The map's cell types: by ordinal, named, or by cluster id where unnamed."""
    ordered = sorted(clusters, key=lambda c: c["ordinal"])
    return [
        CellType(c["ordinal"], c.get("name") or str(c["cluster_id"])) for c in ordered
    ]


def _part_of(cell: dict[str, Any], split: Split) -> str | None:
    if split == "genotype":
        return cell.get("genotype") or None
    if split == "transgene":
        status = (cell.get("facets") or {}).get(TRANSGENE_FACET)
        if status == TRANSGENE_POSITIVE:
            return "Transgene+"
        if status == "False":
            return "Transgene−"
        return status or None
    return None


def splits_offered(cells: Sequence[dict[str, Any]]) -> dict[str, bool]:
    """Which splits the cells can be put through: only those some cell records."""
    return {
        "genotype": any(c.get("genotype") for c in cells),
        "transgene": any((c.get("facets") or {}).get(TRANSGENE_FACET) for c in cells),
    }


def group_cells(
    cells: Sequence[dict[str, Any]], types: Sequence[CellType], split: Split
) -> list[CellGroup]:
    """The cells by cell type in the map's order, "No cell type" last, each split into its
    parts when asked, "Not recorded" last within it."""
    known = {t.ordinal for t in types}
    by_type: dict[int, list[int]] = {}
    orphans: list[int] = []
    for i, cell in enumerate(cells):
        ordinal = cell.get("cluster_ordinal")
        if ordinal in known:
            by_type.setdefault(ordinal, []).append(i)
        else:
            orphans.append(i)

    out: list[CellGroup] = []

    def add(name: str, members: list[int]) -> None:
        if not members:
            return
        if split == "none":
            out.append(CellGroup(name, None, members))
            return
        parts: dict[str, list[int]] = {}
        for i in members:
            parts.setdefault(_part_of(cells[i], split) or NOT_RECORDED, []).append(i)
        for part in sorted(parts, key=lambda p: (p == NOT_RECORDED, p.casefold(), p)):
            out.append(CellGroup(name, part, parts[part]))

    for t in types:
        add(t.name, by_type.get(t.ordinal, []))
    add(NO_CELL_TYPE, orphans)
    return out


def group_stats(values: Sequence[float], cells: Sequence[int]) -> GroupStats | None:
    """The group's cell count, cells expressing (above 0), share, mean, min, quartiles and
    max over all of its cells; None for a group with none."""
    n = len(cells)
    if n == 0:
        return None
    ordered = sorted(values[i] for i in cells)

    def quantile(p: float) -> float:
        h = (n - 1) * p
        lo = math.floor(h)
        hi = min(n - 1, lo + 1)
        return ordered[lo] + (h - lo) * (ordered[hi] - ordered[lo])

    expressing = sum(1 for v in ordered if v > 0)
    return GroupStats(
        cells=n,
        expressing=expressing,
        share=expressing / n,
        mean=sum(ordered) / n,
        min=ordered[0],
        q1=quantile(0.25),
        median=quantile(0.5),
        q3=quantile(0.75),
        max=ordered[-1],
    )
