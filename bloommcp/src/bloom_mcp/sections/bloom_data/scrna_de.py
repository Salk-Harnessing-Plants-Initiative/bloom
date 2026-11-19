"""scrna_de — one scRNA dataset's cell-type markers and genotype DE, as its page shows them."""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import scrna_de_reads as de
from bloom_mcp.data_access.gene_reads import Reads
from bloom_mcp.data_access.page_links import page_url
from bloom_mcp.tools import _ports

from ._common import fix_request, logs_call, reports_de_errors
from ._page import MAX_REPLY_CHARS
from .scrna_de_reply import (
    CellType,
    Comparison,
    DeGene,
    MarkerGene,
    Markers,
    ScrnaDeResult,
    Side,
    fit,
)

# Most genotype comparisons whose genes one call reads (about 6 queries each).
MAX_COMPARISONS = 20
# Seconds one call may spend reading before it returns what it has.
CALL_SECONDS = 60
# A dataset kind with no cells of its own (the website treats it as not found).
REFERENCE_KIND = "reference"
OUT_OF_TIME = "Not read: the call ran out of time."
NOT_TESTED = "Not tested (too few cells or genes), as the DE tab shows it."


class ScrnaDeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: StrictInt = Field(
        ge=1, description="The scRNA dataset's id (scrna_datasets.id)."
    )
    cell_types: list[str] = Field(
        default=[],
        max_length=de.MAX_CELL_TYPES,
        description=f"Up to {de.MAX_CELL_TYPES} cell type names or cluster ids, any case. "
        f"None: the first {de.MAX_CELL_TYPES} in the page's order.",
    )
    kind: Literal["markers", "genotype", "both"] = Field(
        default="markers",
        description="markers: the cluster panel's markers. genotype: genes that change "
        "between genotypes. both: each.",
    )
    contrast: str | None = Field(
        default=None,
        description="Only this genotype contrast, e.g. myb41_vs_Col-0. None: every one.",
    )
    top: StrictInt = Field(
        default=20,
        ge=1,
        le=100,
        description="Genes listed per side of each genotype comparison.",
    )


class _Call:
    def __init__(self) -> None:
        self.deadline = time.monotonic() + CALL_SECONDS
        self.comparisons_read = 0
        self.notes: list[str] = []

    def late(self) -> bool:
        return time.monotonic() >= self.deadline

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


def _dataset(reads: Reads, dataset_id: int) -> dict[str, Any]:
    dataset = de.live_dataset(reads, dataset_id)
    if dataset is None:
        raise fix_request(
            f"No scRNA dataset {dataset_id}.",
            "Use a dataset id from the gene lookup or scrna_datasets.",
        )
    if dataset.get("kind") == REFERENCE_KIND:
        raise fix_request(
            f"Dataset {dataset_id} is a reference atlas with no DE of its own.",
            "Use a dataset with cells of its own.",
        )
    return dataset


_GENE_KEYS = (
    "gene",
    "log2fc",
    "fdr",
    "found_in_one_group_only",
    "likely_found_in_one_group_only",
)


def _gene(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: entry[k] for k in _GENE_KEYS}


def _marker(entry: dict[str, Any]) -> MarkerGene:
    return MarkerGene(
        **_gene(entry),
        symbol=entry["symbol"],
        source=entry["source"],
        pct_in_cell_type=entry["pct_1"],
        pct_elsewhere=entry["pct_2"],
    )


def _sides(
    reads: Reads, call: _Call, comparison: dict[str, Any], top: int
) -> tuple[list[Side], str | None]:
    """The comparison's genes, by the group each is higher in, or why there are none."""
    if not de.is_tested(comparison):
        return [], NOT_TESTED
    if call.late():
        call.note(OUT_OF_TIME + " Ask for fewer cell types.")
        return [], OUT_OF_TIME
    if call.comparisons_read >= MAX_COMPARISONS:
        call.note(
            f"Genes are read for at most {MAX_COMPARISONS} comparisons per call; the "
            "others are not read. Ask for one contrast or fewer cell types."
        )
        return [], "Genes not read: past this call's comparison limit."
    call.comparisons_read += 1
    found = de.de_genes(reads, comparison, top) or {}
    groups = {"up": comparison["group1"], "down": comparison["group2"]}
    sides = []
    for direction, (genes, passing) in found.items():
        up = direction == "up"
        sides.append(
            Side(
                higher_in=groups[direction],
                passing=passing,
                genes=[
                    DeGene(
                        **_gene(g),
                        pct_in_group=g["pct_1"] if up else g["pct_2"],
                        pct_in_other=g["pct_2"] if up else g["pct_1"],
                    )
                    for g in genes
                ],
            )
        )
    return sides, None


def _comparison(
    reads: Reads,
    call: _Call,
    row: dict[str, Any],
    methods: dict[int, str],
    top: int,
) -> Comparison:
    sides, note = _sides(reads, call, row, top)
    return Comparison(
        contrast=row.get("contrast"),
        group1=row["group1"],
        group2=row["group2"],
        cells_group1=row.get("n_group1"),
        cells_group2=row.get("n_group2"),
        method=methods.get(row["run_id"]),
        tested=de.is_tested(row),
        sides=sides,
        note=note,
    )


def _markers(
    reads: Reads,
    call: _Call,
    dataset_id: int,
    clusters: list[str],
    run_ids: list[int],
) -> dict[str, Markers] | None:
    """Each cell type's panel markers; None when the call is out of time."""
    if call.late():
        call.note(OUT_OF_TIME + " Ask for fewer cell types.")
        return None
    found = de.panel_markers(reads, dataset_id, clusters, run_ids)
    return {
        cluster: Markers(
            source=m["from"],
            significant=None if m["n_significant"] is None else int(m["n_significant"]),
            genes=[_marker(g) for g in m["top"]],
        )
        for cluster, m in found.items()
    }


def _cell_type(
    cluster: dict[str, Any],
    markers: dict[str, Markers] | None,
    genotype: list[Comparison],
    kind: str,
) -> CellType:
    note = None
    found = (markers or {}).get(cluster["cluster_id"])
    if kind != "genotype" and found is None:
        note = (
            OUT_OF_TIME
            if markers is None
            else "No markers: no stored markers and no one-vs-rest comparison."
        )
    return CellType(
        cluster_id=cluster["cluster_id"],
        name=cluster.get("name"),
        markers=found,
        genotype=genotype,
        note=note,
    )


def _link(dataset: dict[str, Any], tab: str) -> str | None:
    url = page_url("scrna_datasets", dataset)
    return f"{url}?tab={tab}" if url else None


@as_mcp_tool(input_model=ScrnaDeParams, output_model=ScrnaDeResult)
@logs_call
@reports_de_errors
def scrna_de(params: ScrnaDeParams) -> ScrnaDeResult:
    """The genes that define each cell type of one scRNA dataset, and the genes that change
    between genotypes in it, with the lists and counts its Bloom page shows.

    markers: what the cluster panel lists (its stored top markers, or the top 25 of the
    cell type's one-vs-rest comparison with FDR < 0.05 and log2FC > 0) and how many genes
    are significant. genotype: for each comparison of two genotypes in a cell type (or the
    whole dataset when no cell type is named), the genes passing the DE tab's default cuts,
    FDR < 0.05 and |log2FC| > 0.5, ranked as the DE tab ranks them, listed under the
    group each gene is higher in, with how many pass. A comparison that wasn't tested says
    so. log2fc is group1 over group2. Find a dataset with the gene lookup. Answer with the
    genes and give the user de_tab_url (more genes, other cuts) or cell_types_url (a cell
    type's panel). Notes say what a call left out.
    """
    reads = Reads(_ports.table_reader())
    call = _Call()
    dataset = _dataset(reads, params.dataset_id)
    chosen = de.cell_types(reads, params.dataset_id, params.cell_types)
    clusters = [c["cluster_id"] for c in chosen.chosen]
    runs = de.runs(reads, params.dataset_id)
    run_ids = [r["id"] for r in runs]
    methods = {r["id"]: r.get("method") for r in runs}
    markers = (
        _markers(reads, call, params.dataset_id, clusters, run_ids)
        if params.kind != "genotype"
        else {}
    )
    genotype: list[dict[str, Any]] = []
    if params.kind != "markers":
        genotype = de.genotype(
            reads, run_ids, clusters, params.contrast, whole=not params.cell_types
        )

    def compare(rows: list[dict[str, Any]]) -> list[Comparison]:
        # The DE tab lists a cell type's comparisons by contrast.
        rows = sorted(rows, key=lambda r: (r["contrast"] is None, r["contrast"] or ""))
        return [_comparison(reads, call, r, methods, params.top) for r in rows]

    cell_types = [
        _cell_type(
            c,
            markers,
            compare([g for g in genotype if g["cluster_id"] == c["cluster_id"]]),
            params.kind,
        )
        for c in chosen.chosen
    ]
    whole = compare([g for g in genotype if g["cluster_id"] is None])
    if not runs:
        call.note("This dataset has no complete DE run.")
    if reads.incomplete:
        call.note(
            "Some reads hit Bloom's row limit, so lists may be incomplete; ask for "
            "fewer cell types or one contrast."
        )
    return fit(
        ScrnaDeResult(
            dataset_id=params.dataset_id,
            dataset_name=dataset["name"],
            de_tab_url=_link(dataset, "de"),
            cell_types_url=_link(dataset, "umap"),
            cell_types=cell_types,
            whole_dataset=whole,
            unknown_cell_types=chosen.unknown,
            left_out_cell_types=[c["cluster_id"] for c in chosen.left_out],
            incomplete=bool(reads.incomplete),
            notes=call.notes,
        ),
        MAX_REPLY_CHARS,
    )
