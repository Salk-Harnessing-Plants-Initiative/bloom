"""scrna_de — one scRNA dataset's cell-type markers and genotype DE, as its page shows them."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from bloom_mcp.contract import as_mcp_tool
from bloom_mcp.data_access import scrna_de_reads as de
from bloom_mcp.data_access.gene_reads import Reads
from bloom_mcp.data_access.page_links import page_url
from bloom_mcp.tools import _ports

from . import _scrna_dataset
from . import scrna_de_build as build
from ._common import logs_call, reports_de_errors
from ._page import MAX_REPLY_CHARS
from .scrna_de_reply import CellType, Comparison, Markers, ScrnaDeResult, fit

# Most genotype comparisons whose genes one call reads (up to about 17 queries each).
MAX_COMPARISONS = 20
# Seconds one call may spend reading, well inside a client's wait for a reply.
CALL_SECONDS = 40
DEFAULT_TOP = 20
MAX_TOP = 100
NO_GENOTYPE = "No genotype comparison for this cell type."
NO_MARKERS = "No markers: no stored markers and no one-vs-rest comparison."


class ScrnaDeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: StrictInt = Field(
        ge=1, description="The scRNA dataset's id (scrna_datasets.id)."
    )
    cell_types: list[Annotated[str, Field(max_length=200)]] = Field(
        default=[],
        max_length=de.MAX_CELL_TYPES,
        description=f"Up to {de.MAX_CELL_TYPES} cell type names or cluster ids, any case. "
        f"Empty: the first {de.MAX_CELL_TYPES} in the page's order, and the whole dataset.",
    )
    kind: Literal["markers", "genotype", "both"] = Field(
        default="markers",
        description="markers: the cluster panel's markers. genotype: genes that change "
        "between genotypes. both: each.",
    )
    contrast: str | None = Field(
        default=None,
        max_length=200,
        description="Only this genotype contrast, any case, e.g. myb41_vs_Col-0. None: "
        "every one. contrasts lists the ones there are.",
    )
    top: StrictInt = Field(
        default=DEFAULT_TOP,
        ge=1,
        le=MAX_TOP,
        description="Genes listed per side of each genotype comparison.",
    )


def _genotype_rows(
    reads: Reads,
    call: build.Call,
    params: ScrnaDeParams,
    run_ids: list[int],
    clusters: list[str],
    whole: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    """The genotype comparisons to show, and every contrast these cell types have."""
    rows = de.genotype(reads, run_ids, clusters, None, whole=whole)
    contrasts = sorted(
        {r["contrast"] for r in rows if r["contrast"]}, key=de.name_order
    )
    if not rows:
        call.note(
            "This dataset has no genotype comparison."
            if whole
            else "None of these cell types has a genotype comparison."
        )
    if params.contrast is None or not rows:
        return rows, contrasts
    wanted = params.contrast.strip().casefold()
    kept = [r for r in rows if (r["contrast"] or "").casefold() == wanted]
    if not kept:
        call.note(
            f"No comparison has contrast {build.text(params.contrast)!r} here; the "
            f"contrasts are {', '.join(build.text(c) for c in contrasts)}."
        )
    return kept, contrasts


def _cell_type(
    cluster: dict[str, Any],
    markers: Markers | None,
    genotype: list[Comparison],
    params: ScrnaDeParams,
    out_of_time: bool,
) -> CellType:
    notes = []
    if params.kind != "genotype" and markers is None:
        notes.append(build.OUT_OF_TIME if out_of_time else NO_MARKERS)
    if params.kind != "markers" and not genotype:
        notes.append(
            NO_GENOTYPE
            if params.contrast is None
            else f"No {build.text(params.contrast.strip())} comparison for this cell type."
        )
    return CellType(
        cluster_id=build.text(cluster["cluster_id"]),
        name=build.text(cluster.get("name")),
        markers=markers,
        genotype=genotype,
        note=" ".join(notes) or None,
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
    are significant. genotype: for each comparison of two genotypes in a cell type, and
    over the whole dataset when no cell type is named, the genes passing the DE tab's
    default cuts, FDR < 0.05 and |log2FC| > 0.5, ranked by FDR then gene name, listed
    under the group each gene is higher in, with how many pass. A comparison that wasn't
    tested says so; one where no gene passes is not evidence of no difference. log2fc is
    group1 over group2. Find a dataset with the gene lookup. Answer with the genes and
    give the user de_tab_url (more genes, other cuts) or cell_types_url (a cell type's
    panel). Notes say what a call left out.
    """
    reads = Reads(_ports.table_reader())
    call = build.Call(CALL_SECONDS, MAX_COMPARISONS)
    dataset = _scrna_dataset.dataset(reads, params.dataset_id, lacks="DE")
    chosen = de.cell_types(reads, params.dataset_id, params.cell_types)
    clusters = [c["cluster_id"] for c in chosen.chosen]
    whole = not any(name.strip() for name in params.cell_types)
    runs = de.runs(reads, params.dataset_id)
    run_ids = [r["id"] for r in runs]
    methods = {r["id"]: r.get("method") for r in runs}
    if not runs:
        call.note("This dataset has no complete DE run.")
    markers: dict[str, Markers | None] = {}
    if params.kind != "genotype":
        markers = build.markers(reads, call, params.dataset_id, clusters, run_ids)
    if params.kind == "markers" and params.contrast is not None:
        call.note(
            "contrast filters genotype comparisons only; this call reads markers."
        )
    rows: list[dict[str, Any]] = []
    contrasts: list[str] = []
    if params.kind != "markers":
        rows, contrasts = _genotype_rows(reads, call, params, run_ids, clusters, whole)

    def compare(own: list[dict[str, Any]]) -> list[Comparison]:
        # The DE tab lists comparisons by contrast, a missing one first.
        own = sorted(
            own,
            key=lambda r: (
                r["contrast"] is not None,
                de.name_order(r["contrast"] or ""),
            ),
        )
        return [build.comparison(reads, call, r, methods, params.top) for r in own]

    # The whole dataset first, so the comparison limit never leaves it unread.
    whole_dataset = compare([r for r in rows if r["cluster_id"] is None])
    cell_types = [
        _cell_type(
            c,
            markers.get(c["cluster_id"]),
            compare([r for r in rows if r["cluster_id"] == c["cluster_id"]]),
            params,
            c["cluster_id"] in markers and markers[c["cluster_id"]] is None,
        )
        for c in chosen.chosen
    ]
    if reads.incomplete:
        call.note(
            "Some reads hit Bloom's row limit, so lists may be incomplete; ask for "
            "fewer cell types or one contrast."
        )
    return fit(
        ScrnaDeResult(
            dataset_id=params.dataset_id,
            dataset_name=build.text(dataset["name"]),
            de_tab_url=_link(dataset, "de"),
            cell_types_url=_link(dataset, "umap"),
            cell_types=cell_types,
            whole_dataset=whole_dataset,
            contrasts=[build.text(c) for c in contrasts],
            unknown_cell_types=[build.text(u) for u in chosen.unknown],
            left_out_cell_types=[c["cluster_id"] for c in chosen.left_out],
            incomplete=bool(reads.incomplete),
            notes=call.notes,
        ),
        MAX_REPLY_CHARS,
    )
