"""scrna_gene_cells' reply: the page of cells, its totals, and fitting it to one reply."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

# Room kept for next_offset's digits and the cut-short note's comma.
_SLACK = 32
CUT_SHORT = "The page ended early to fit one reply; follow next_offset for the rest."


class CellRow(BaseModel):
    cell: int = Field(description="The cell's position in the dataset's cell order.")
    value: float
    cell_type: str
    genotype: str | None
    replicate: str | None
    facets: Any = Field(description="The cell's other recorded labels, as stored.")
    x: float = Field(description="UMAP x.")
    y: float = Field(description="UMAP y.")


class GeneCellsResult(BaseModel):
    dataset_id: int
    dataset_name: str
    gene: str | None = Field(description="The gene as asked for.")
    gene_name: str | None = Field(description="The dataset's name for the gene.")
    cells_in_scope: int = Field(description="Cells of the cell types asked for (all).")
    cells_expressing: int | None = Field(
        description="Of those, cells with a value above 0; None when no values were read "
        "(no gene, or a note says why)."
    )
    offset: int
    returned: int = Field(description="Cells on this page.")
    cells: list[CellRow] = Field(description="Expressing cells, highest value first.")
    next_offset: int | None = Field(description="Where the next page starts, if any.")
    page_url: str | None = Field(
        description="The dataset's UMAP on Bloom, coloured by the gene."
    )
    notes: list[str]


def _size(value: Any) -> int:
    return len(json.dumps(value, default=str))


def fit(
    result: GeneCellsResult, rows: list[CellRow], more: bool, budget: int
) -> GeneCellsResult:
    """`result` with as many of `rows` as fit `budget` characters; `more` says cells remain
    past `rows`. A page cut short says so, and next_offset starts at the first cell left out.
    """
    base = _size(result.model_dump()) + _size(CUT_SHORT) + _SLACK
    kept: list[CellRow] = []
    for row in rows:
        base += _size(row.model_dump()) + 2
        if kept and base > budget:
            result.notes.append(CUT_SHORT)
            more = True
            break
        kept.append(row)
    result.cells, result.returned = kept, len(kept)
    result.next_offset = result.offset + len(kept) if more else None
    return result
