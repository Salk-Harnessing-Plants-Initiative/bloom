"""scrna_gene_cells — one gene's expressing cells in one scRNA dataset, highest first, in pages."""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)

from bloom_mcp.contract import WithImages, as_mcp_tool
from bloom_mcp.data_access import expression_counts, expression_stats, gene_reads
from bloom_mcp.data_access import scrna_de_reads as de
from bloom_mcp.data_access.page_links import umap_page
from bloom_mcp.data_access.table_ports import Filter
from bloom_mcp.tools import _ports

from . import _scrna_dataset, gene_cells_plot
from ._common import logs_call, reports_lookup_errors
from ._page import MAX_REPLY_CHARS, _cut
from .gene_cells_reply import CellRow, GeneCellsResult, fit

DEFAULT_LIMIT = 500
MAX_LIMIT = 2000
MAX_CELL_TYPES = 10
# Seconds one call may spend reading, well inside a client's wait for a reply.
CALL_SECONDS = 40
# Decimal places kept in each value, as the expression tool keeps them.
DIGITS = 4
OUT_OF_TIME = "Not read: the call ran out of time. Try again, or ask about fewer cells."
# The website's colour-bar label for a dataset that records no units.
UNITS_FALLBACK = "log-normalized"

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class GeneCellsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: StrictInt = Field(
        ge=1, description="The scRNA dataset's id (scrna_datasets.id)."
    )
    gene: Name | None = Field(
        default=None,
        description="One gene name, symbol (MYB41) or id (AT4G28110), as the gene lookup "
        "gives it. Needed unless color_by is a group.",
    )
    cell_types: list[Annotated[str, StringConstraints(max_length=200)]] = Field(
        default_factory=list,
        max_length=MAX_CELL_TYPES,
        description=f"Up to {MAX_CELL_TYPES} cell types by name or cluster id, any case; "
        "empty for every cell.",
    )
    offset: StrictInt = Field(
        default=0, ge=0, description="Cells to skip: next_offset."
    )
    limit: StrictInt = Field(
        default=DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=f"Cells per page, at most {MAX_LIMIT}.",
    )
    plot: Literal["umap"] | None = Field(
        default=None,
        description='"umap" also sends one image of the dataset\'s UMAP, as Bloom draws it. '
        "Ask on the first page only; it costs reply tokens.",
    )
    color_by: Literal["gene", "cell_type", "genotype", "replicate"] = Field(
        default="gene",
        description="What the UMAP is coloured by. Anything but gene draws the UMAP "
        "without plot, and then gene may be left out.",
    )

    @model_validator(mode="after")
    def _gene_for_gene_colours(self) -> GeneCellsParams:
        if self.color_by == "gene" and self.gene is None:
            raise ValueError(
                'Give a gene, or color_by "cell_type", "genotype" or "replicate".'
            )
        return self


def _scope(
    cells: list[dict[str, Any]], clusters: list[dict[str, Any]], asked: list[str]
) -> tuple[list[str], set[int] | None, list[str]]:
    """Each cell's cell type, the positions in scope (None: every cell), and notes. A name
    or cluster id picks each cluster it names, as the DE tool matches them."""
    types = expression_stats.cell_types(clusters)
    name_of = {t.ordinal: t.name for t in types}
    names = [
        name_of.get(c.get("cluster_ordinal"), expression_stats.NO_CELL_TYPE)
        for c in cells
    ]
    wanted = list(dict.fromkeys(a.strip() for a in asked if a.strip()))
    if not wanted:
        return names, None, []
    chosen: set[int] = set()
    orphans = False
    unknown = []
    for w in wanted:
        key = w.casefold()
        found = {c["ordinal"] for c in clusters if de.is_named(c, key)}
        chosen |= found
        if key == expression_stats.NO_CELL_TYPE.casefold():
            orphans = True
        elif not found:
            unknown.append(w)
    notes = []
    if unknown:
        note = f"No cell type named {', '.join(_cut(u)[0] for u in unknown)} in this dataset."
        if not chosen and not orphans:
            listed = ", ".join(_cut(t.name)[0] for t in types)
            note += f" Its cell types: {listed}."
        notes.append(note)
    scope = {
        i
        for i, c in enumerate(cells)
        if c.get("cluster_ordinal") in chosen
        or (orphans and c.get("cluster_ordinal") not in name_of)
    }
    return names, scope, notes


def _gene(
    reads: gene_reads.Reads, dataset_id: int, name: str
) -> tuple[dict[str, Any] | None, list[str]]:
    """The dataset's gene `name` names, and a note naming any other match."""
    found = _scrna_dataset.matches(reads, dataset_id, name)
    if not found:
        return None, ["Not a gene in this dataset."]
    first, others = found[0], [g["gene_name"] for g in found[1:]]
    if not others:
        return first, []
    return first, [
        f"{name} also matches {', '.join(others)}; these cells are "
        f"{first['gene_name']}'s. Ask for another by its name."
    ]


def _row(i: int, value: float, cell: dict[str, Any], cell_type: str) -> CellRow:
    return CellRow(
        cell=i,
        value=round(value, DIGITS),
        cell_type=_cut(cell_type)[0],
        genotype=_cut(cell.get("genotype"))[0],
        replicate=_cut(cell.get("replicate"))[0],
        facets=_cut(cell.get("facets"))[0],
        x=cell["x"],
        y=cell["y"],
    )


@as_mcp_tool(input_model=GeneCellsParams, output_model=GeneCellsResult)
@logs_call
@reports_lookup_errors
def scrna_gene_cells(params: GeneCellsParams) -> GeneCellsResult | WithImages:
    """One gene's value in each cell of one scRNA dataset that expresses it (value above 0),
    highest value first, with each cell's cell type, genotype, replicate, other labels and
    UMAP position.

    Use it for questions about individual cells: "where on the UMAP are the cells with the
    most MYB41?", "do the high cells come from one replicate?". For a gene's numbers per cell
    type, use bloom_data_gene_expression instead. cells_in_scope and cells_expressing give
    the totals; read the first page and the totals, and follow next_offset only when the
    question needs every cell. cell_types narrows to some cell types. page_url opens the
    dataset's UMAP on Bloom coloured by the gene. plot="umap" also sends that UMAP as an
    image, drawn from the same values; color_by "cell_type", "genotype" or "replicate" sends
    the UMAP coloured by that instead (no gene needed). If an image can't be drawn, a note
    says why.
    """
    deadline = time.monotonic() + CALL_SECONDS
    reads = gene_reads.Reads(_ports.table_reader())
    dataset = _scrna_dataset.dataset(reads, params.dataset_id)
    cells = _scrna_dataset.cells(params.dataset_id)
    clusters = reads.rows(
        "scrna_clusters", [Filter("dataset_id", "eq", params.dataset_id)]
    )
    names, scope, notes = _scope(cells, clusters, params.cell_types)
    in_scope = range(len(cells)) if scope is None else sorted(scope)

    gene, values = None, None
    if params.gene is not None:
        gene, gene_notes = _gene(reads, params.dataset_id, params.gene)
        notes += gene_notes
    by_gene = params.color_by == "gene"
    result = GeneCellsResult(
        dataset_id=params.dataset_id,
        dataset_name=dataset["name"],
        gene=params.gene,
        gene_name=gene["gene_name"] if gene else None,
        units=dataset.get("expression_units") or UNITS_FALLBACK,
        cells_in_scope=len(in_scope),
        cells_expressing=None,
        offset=params.offset,
        returned=0,
        cells=[],
        next_offset=None,
        page_url=umap_page(dataset, gene["gene_name"] if gene and by_gene else None),
        notes=notes,
    )
    if gene is not None:
        values = _values(
            reads, params.dataset_id, gene, len(cells), deadline, result.notes
        )

    rows: list[CellRow] = []
    more = False
    if values is not None:
        expressing = sorted(
            (i for i in in_scope if values[i] > 0), key=lambda i: (-values[i], i)
        )
        result.cells_expressing = len(expressing)
        end = params.offset + params.limit
        rows = [
            _row(i, values[i], cells[i], names[i])
            for i in expressing[params.offset : end]
        ]
        more = len(expressing) > end

    image = None
    if not by_gene:
        title = f"{dataset['name']} by {params.color_by.replace('_', ' ')}"
        image, reasons = gene_cells_plot.group_umap(
            cells, clusters, scope, params.color_by, title, deadline
        )
        result.notes += reasons
    elif params.plot == "umap" and values is not None and gene is not None:
        title = f"{gene['gene_name']} in {dataset['name']}"
        image, reasons = gene_cells_plot.gene_umap(
            cells, clusters, values, scope, title, result.units, deadline
        )
        result.notes += reasons
    result = fit(result, rows, more, MAX_REPLY_CHARS)
    return WithImages(result, [image]) if image else result


def _values(
    reads: gene_reads.Reads,
    dataset_id: int,
    gene: dict[str, Any],
    cell_count: int,
    deadline: float,
    notes: list[str],
) -> list[float] | None:
    """The gene's value in every cell, or None with a note saying why."""
    if time.monotonic() >= deadline:
        notes.append(OUT_OF_TIME)
        return None
    try:
        return _scrna_dataset.gene_values(reads, dataset_id, gene, cell_count)
    except expression_counts.CountsProblem as problem:
        notes.append(str(problem))
        return None
