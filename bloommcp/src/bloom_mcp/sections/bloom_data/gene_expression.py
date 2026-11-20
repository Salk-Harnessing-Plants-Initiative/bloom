"""gene_expression — genes' expression per cell type in one scRNA dataset, with the plot's link."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import expression_counts, expression_stats, gene_reads
from bloom_mcp.data_access.page_links import expression_page
from bloom_mcp.data_access.table_ports import Filter
from bloom_mcp.tools import _ports

from . import _scrna_dataset
from ._common import fix_request, logs_call, reports_lookup_errors
from ._page import MAX_REPLY_CHARS

MAX_GENES = 5
# Decimal places kept in the statistics: plenty for log-normalised values, short replies.
DIGITS = 4


class GeneExpressionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: StrictInt = Field(
        ge=1, description="The scRNA dataset's id (scrna_datasets.id)."
    )
    genes: list[str] = Field(
        min_length=1,
        max_length=MAX_GENES,
        description=f"Up to {MAX_GENES} gene names, symbols (MYB41) or ids (AT4G28110), as the "
        "gene lookup gives them.",
    )
    split: Literal["none", "genotype", "transgene"] = Field(
        default="none",
        description="Also split each cell type by genotype or by transgene status, as the "
        "website can.",
    )


class GroupRow(BaseModel):
    cell_type: str
    part: str | None = Field(description="The genotype or transgene status when split.")
    cells: int
    expressing: int = Field(description="Cells with a value above 0.")
    share: float = Field(description="expressing / cells.")
    mean: float
    min: float
    q1: float
    median: float
    q3: float
    max: float


class GeneExpression(BaseModel):
    requested: str
    gene_name: str | None = Field(
        description="The dataset's name for the gene, when found."
    )
    groups: list[GroupRow]
    note: str | None = Field(
        description="Why there are no numbers, when there are none."
    )


class GeneExpressionResult(BaseModel):
    dataset_id: int
    dataset_name: str
    cells: int
    split: str
    splits_offered: dict[str, bool]
    page_url: str | None = Field(
        description="The dataset's Genes by cell type plot, opened on the genes found."
    )
    genes: list[GeneExpression]
    returned: int = Field(description="Genes with numbers.")
    notes: list[str] = Field(description="Genes matched but not read in this call.")


def _matches(
    reads: gene_reads.Reads, dataset_id: int, name: str
) -> list[dict[str, Any]]:
    """The dataset's genes `name` names: exact names first, else any case."""
    in_dataset = Filter("dataset_id", "eq", dataset_id)
    names = gene_reads.scrna_names(reads, name)
    found = reads.rows("scrna_genes", [in_dataset, Filter("gene_name", "in", names)])
    if not found:
        found = reads.rows(
            "scrna_genes", [in_dataset, Filter("gene_name", "iequals", name)]
        )
    return sorted(found, key=lambda g: g["gene_name"])


def _read_at_most(
    reads: gene_reads.Reads, dataset_id: int, names: list[str]
) -> tuple[list[tuple[str, dict[str, Any] | None]], list[str]]:
    """(requested name, dataset gene or None) for up to MAX_GENES genes in all, and the
    names of genes matched past that."""
    picked: list[tuple[str, dict[str, Any] | None]] = []
    left_out: list[str] = []
    for name in names:
        matches = _matches(reads, dataset_id, name)
        if not matches:
            picked.append((name, None))
        read = {gene["id"] for _, gene in picked if gene is not None}
        room = MAX_GENES - sum(1 for _, gene in picked if gene is not None)
        picked += [(name, gene) for gene in matches[:room]]
        # A gene already read under another name isn't "not read".
        left_out += [g["gene_name"] for g in matches[room:] if g["id"] not in read]
    return picked, list(dict.fromkeys(left_out))


def _size(result: GeneExpressionResult) -> int:
    return len(json.dumps(result.model_dump(), default=str))


def _fit(result: GeneExpressionResult) -> GeneExpressionResult:
    """`result` with genes' numbers left out from the last gene back until it fits one
    reply; each such gene says so in its note."""
    leave_out = "Left out to fit one reply: ask for this gene in its own call"
    if result.split != "none":
        leave_out += " or without split"
    for gene in reversed(result.genes):
        if _size(result) <= MAX_REPLY_CHARS:
            break
        if gene.groups:
            gene.groups, gene.note = [], leave_out + "."
    result.returned = sum(1 for g in result.genes if g.groups)
    return result


def _round(stats: expression_stats.GroupStats) -> dict[str, Any]:
    return {
        k: round(v, DIGITS) if isinstance(v, float) else v
        for k, v in stats.__dict__.items()
    }


def _expression(
    reads: gene_reads.Reads,
    dataset_id: int,
    gene: dict[str, Any],
    requested: str,
    cell_count: int,
    groups: list[expression_stats.CellGroup],
) -> GeneExpression:
    counts = reads.rows(
        "scrna_counts",
        [Filter("dataset_id", "eq", dataset_id), Filter("gene_id", "eq", gene["id"])],
    )
    path = next(
        (c["counts_object_path"] for c in counts if c.get("counts_object_path")), None
    )
    if path is None:
        return GeneExpression(
            requested=requested,
            gene_name=gene["gene_name"],
            groups=[],
            note="This dataset stores no expression for this gene.",
        )
    try:
        values = expression_counts.gene_values(_ports.file_reader(), path, cell_count)
    except expression_counts.CountsProblem as problem:
        return GeneExpression(
            requested=requested,
            gene_name=gene["gene_name"],
            groups=[],
            note=str(problem),
        )
    rows = []
    for group in groups:
        stats = expression_stats.group_stats(values, group.cells)
        if stats is not None:
            rows.append(
                GroupRow(cell_type=group.cell_type, part=group.part, **_round(stats))
            )
    return GeneExpression(
        requested=requested, gene_name=gene["gene_name"], groups=rows, note=None
    )


@as_mcp_tool(input_model=GeneExpressionParams, output_model=GeneExpressionResult)
@logs_call
@reports_lookup_errors
def gene_expression(params: GeneExpressionParams) -> GeneExpressionResult:
    """How much each gene is expressed in each cell type of one scRNA dataset, with a link
    to the dataset's plot of those genes.

    For each cell type (and genotype or transgene status, with split): cells, cells
    expressing (value above 0), share expressing, mean, min, quartiles, median and max,
    counting every cell with zeros included, as Bloom's Genes by cell type plot does. Use
    it for "where is this gene expressed?" or "is it higher in the transgene?". Find the
    datasets that measured a gene with the gene lookup. Answer with the numbers and give
    the user page_url to see the plot. A gene with no numbers says why in its note. At most
    5 genes are read per call; notes name any other matches.
    """
    requested = [g.strip() for g in params.genes if g.strip()]
    if not requested:
        raise fix_request("Give at least one gene name.", "For example AT4G28110.")
    reads = gene_reads.Reads(_ports.table_reader())
    dataset = _scrna_dataset.dataset(reads, params.dataset_id)
    cells = _scrna_dataset.cells(params.dataset_id)
    clusters = reads.rows(
        "scrna_clusters", [Filter("dataset_id", "eq", params.dataset_id)]
    )
    groups = expression_stats.group_cells(
        cells, expression_stats.cell_types(clusters), params.split
    )

    picked, left_out = _read_at_most(
        reads, params.dataset_id, list(dict.fromkeys(requested))
    )
    results = [
        _expression(reads, params.dataset_id, gene, name, len(cells), groups)
        if gene is not None
        else GeneExpression(
            requested=name,
            gene_name=None,
            groups=[],
            note="Not a gene in this dataset.",
        )
        for name, gene in picked
    ]
    notes = (
        [
            f"Reads at most {MAX_GENES} genes per call; not read: "
            + ", ".join(left_out)
            + ". Ask for them by name in another call."
        ]
        if left_out
        else []
    )

    shown = [r.gene_name for r in results if r.groups and r.gene_name]
    return _fit(
        GeneExpressionResult(
            dataset_id=params.dataset_id,
            dataset_name=dataset["name"],
            cells=len(cells),
            split=params.split,
            splits_offered=expression_stats.splits_offered(cells),
            page_url=expression_page(dataset, list(dict.fromkeys(shown))),
            genes=results,
            returned=sum(1 for r in results if r.groups),
            notes=notes,
        )
    )
