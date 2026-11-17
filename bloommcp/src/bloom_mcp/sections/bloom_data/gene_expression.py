"""gene_expression — genes' expression per cell type in one scRNA dataset, with the plot's link."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import expression_counts, expression_stats, gene_reads
from bloom_mcp.data_access.page_links import expression_page
from bloom_mcp.data_access.table_ports import Filter
from bloom_mcp.tools import _ports

from ._common import fix_request, logs_call, reports_lookup_errors

MAX_GENES = 5
# Most cells a dataset may have for this tool to read them all; past it, the page link only.
MAX_CELLS = 500_000
# Decimal places kept in the statistics: plenty for log-normalised values, short replies.
DIGITS = 4
# A dataset kind with no cells of its own (the website treats it as not found).
REFERENCE_KIND = "reference"
CELL_ARRAYS = "scrna_cell_arrays"


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


def _dataset(reads: gene_reads.Reads, dataset_id: int) -> dict[str, Any]:
    found = reads.rows(
        "scrna_datasets",
        [Filter("id", "eq", dataset_id), Filter("deleted_at", "is_null", True)],
    )
    if not found:
        raise fix_request(
            f"No scRNA dataset {dataset_id}.",
            "Use a dataset id from the gene lookup or scrna_datasets.",
        )
    if found[0].get("kind") == REFERENCE_KIND:
        raise fix_request(
            f"Dataset {dataset_id} is a reference atlas with no cells of its own.",
            "Use one of the datasets the gene lookup lists for the gene.",
        )
    return found[0]


def _cells(dataset_id: int) -> list[dict[str, Any]]:
    cells = _ports.table_reader().call(
        CELL_ARRAYS, {"ds_id": dataset_id}, [], MAX_CELLS + 1, 0
    )
    if len(cells) > MAX_CELLS:
        raise fix_request(
            f"Dataset {dataset_id} has over {MAX_CELLS:,} cells, more than this tool reads.",
            "Give the user the dataset's page link to see the genes there.",
        )
    return cells


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
    the user page_url to see the plot. A gene with no numbers says why in its note.
    """
    requested = [g.strip() for g in params.genes if g.strip()]
    if not requested:
        raise fix_request("Give at least one gene name.", "For example AT4G28110.")
    reads = gene_reads.Reads(_ports.table_reader())
    dataset = _dataset(reads, params.dataset_id)
    cells = _cells(params.dataset_id)
    clusters = reads.rows(
        "scrna_clusters", [Filter("dataset_id", "eq", params.dataset_id)]
    )
    groups = expression_stats.group_cells(
        cells, expression_stats.cell_types(clusters), params.split
    )

    results: list[GeneExpression] = []
    for name in dict.fromkeys(requested):
        matches = _matches(reads, params.dataset_id, name)
        if not matches:
            results.append(
                GeneExpression(
                    requested=name,
                    gene_name=None,
                    groups=[],
                    note="Not a gene in this dataset.",
                )
            )
        for gene in matches[:MAX_GENES]:
            results.append(
                _expression(reads, params.dataset_id, gene, name, len(cells), groups)
            )

    shown = [r.gene_name for r in results if r.groups and r.gene_name]
    return GeneExpressionResult(
        dataset_id=params.dataset_id,
        dataset_name=dataset["name"],
        cells=len(cells),
        split=params.split,
        splits_offered=expression_stats.splits_offered(cells),
        page_url=expression_page(dataset, list(dict.fromkeys(shown))),
        genes=results,
        returned=sum(1 for r in results if r.groups),
    )
