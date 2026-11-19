"""The DE tool's reads: a dataset's cell types, complete runs, chosen comparisons and each
comparison's passing genes, read as Bloom's DE tab reads them."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from . import de_choice, gene_reads
from .gene_reads import (
    DE_FDR_CUT,
    DE_LOG2FC_CUT,
    OFF_SCALE_LOG2FC,
    Reads,
)
from .table_ports import Filter, Sort

__all__ = ["DE_FDR_CUT", "DE_LOG2FC_CUT", "OFF_SCALE_LOG2FC"]

# Most cell types one call returns.
MAX_CELL_TYPES = 10
# What a one-vs-rest comparison's other side is called (REST_GROUP in cluster-markers.ts).
REST_GROUP = "rest"

Direction = Literal["up", "down"]


@dataclass
class CellTypes:
    """The cell types a call covers, in the website's order, and what it couldn't."""

    chosen: list[dict[str, Any]] = field(default_factory=list)
    left_out: list[dict[str, Any]] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


def cell_types(reads: Reads, dataset_id: int, wanted: list[str]) -> CellTypes:
    """The dataset's cell types by cluster id or name in any case, every cluster carrying
    a name; all of them when none are named. At most MAX_CELL_TYPES."""
    clusters = reads.rows("scrna_clusters", [Filter("dataset_id", "eq", dataset_id)])
    clusters.sort(key=lambda c: (c.get("ordinal") is None, c.get("ordinal"), c["id"]))
    found = CellTypes()
    if wanted:
        keys = [w.strip() for w in wanted]
        found.unknown = [k for k in keys if not any(_names(c, k) for c in clusters)]
        clusters = [c for c in clusters if any(_names(c, k) for k in keys)]
    found.chosen = clusters[:MAX_CELL_TYPES]
    found.left_out = clusters[MAX_CELL_TYPES:]
    return found


def _names(cluster: dict[str, Any], key: str) -> bool:
    name = cluster.get("name")
    return cluster["cluster_id"] == key or (
        name is not None and name.lower() == key.lower()
    )


def runs(reads: Reads, dataset_id: int) -> list[dict[str, Any]]:
    """The dataset's complete DE runs, newest first, with their method."""
    order = de_choice.newest_first(reads, [dataset_id]).get(dataset_id, [])
    by_id = {r["id"]: r for r in reads.rows_in("scrna_de_runs", "id", order)}
    return [by_id[i] for i in order]


def is_tested(comparison: dict[str, Any]) -> bool:
    """The website's isTested: not marked untested, and some genes tested."""
    return (
        comparison.get("tested") is not False and comparison.get("n_genes_tested") != 0
    )


def _comparisons(
    reads: Reads, run_ids: list[int], filters: list[Filter]
) -> list[dict[str, Any]]:
    """Comparisons in `run_ids` matching `filters`, newest run first."""
    if not run_ids:
        return []
    found, more = reads.page(
        "scrna_de",
        [Filter("run_id", "in", run_ids), *filters],
        [Sort("run_id", descending=True), Sort("id")],
        limit=gene_reads.MAX_ROWS,
    )
    if more:
        reads.incomplete.add("scrna_de")
    return found


def one_vs_rest(
    reads: Reads, run_ids: list[int], cluster_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Each cell type's tested one-vs-rest comparison, chosen as findOneVsRest chooses."""
    if not cluster_ids:
        return {}
    found = _comparisons(
        reads,
        run_ids,
        [
            Filter("group_kind", "eq", "cluster"),
            Filter("group2", "eq", REST_GROUP),
            Filter("tested", "eq", True),
            Filter("cluster_id", "in", cluster_ids),
        ],
    )
    own = [c for c in found if c["group1"] == c["cluster_id"] and is_tested(c)]
    return de_choice.one_vs_rest(own, run_ids)


def genotype(
    reads: Reads,
    run_ids: list[int],
    cluster_ids: list[str],
    contrast: str | None,
    *,
    whole: bool,
) -> list[dict[str, Any]]:
    """Genotype comparisons of `cluster_ids` (and the whole dataset when `whole`), tested
    or not, the newest run winning per cell type and contrast (fetchAllComparisons)."""
    kind = [Filter("group_kind", "eq", "genotype")]
    if contrast is not None:
        kind.append(Filter("contrast", "eq", contrast))
    found = []
    if cluster_ids:
        found += _comparisons(
            reads, run_ids, [*kind, Filter("cluster_id", "in", cluster_ids)]
        )
    if whole:
        found += _comparisons(
            reads, run_ids, [*kind, Filter("cluster_id", "is_null", True)]
        )
    return de_choice.genotype(found, run_ids)


def _passing_filters(de_id: int, direction: Direction) -> list[Filter]:
    side = (
        [Filter("log2fc", "gt", DE_LOG2FC_CUT), Filter("log2fc", "neq", "NaN")]
        if direction == "up"
        else [Filter("log2fc", "lt", -DE_LOG2FC_CUT)]
    )
    return [Filter("de_id", "eq", de_id), Filter("fdr", "lt", DE_FDR_CUT), *side]


def passing(
    reads: Reads, de_id: int, direction: Direction, top: int
) -> tuple[list[dict[str, Any]], int]:
    """The comparison's `top` passing genes in one direction, ranked as the website's
    summary ranks them (FDR, then gene name, then gene id), and how many pass in all."""
    filters = _passing_filters(de_id, direction)
    order = [Sort("fdr"), Sort("id")]
    rows, more = reads.page("scrna_de_genes", filters, order, limit=top)
    if more and rows:
        # Genes tied at the cut: the website breaks FDR ties by name, not row id.
        tied = reads.rows(
            "scrna_de_genes", [*filters, Filter("fdr", "eq", rows[-1]["fdr"])]
        )
        rows = list({r["id"]: r for r in [*rows, *tied]}.values())
    names = _gene_names(reads, [r["gene_id"] for r in rows])
    ranked = sorted(
        rows, key=lambda r: (r["fdr"], names.get(r["gene_id"]) or "", r["gene_id"])
    )
    count = reads.reader.count("scrna_de_genes", "id", filters, exact=True) or 0
    return [_gene(r, names.get(r["gene_id"])) for r in ranked[:top]], count


def _gene_names(reads: Reads, gene_ids: list[int]) -> dict[int, str]:
    rows = reads.rows_in("scrna_genes", "id", gene_ids)
    return {r["id"]: r["gene_name"] for r in rows}


def _gene(row: dict[str, Any], name: str | None) -> dict[str, Any]:
    log2fc = None if row.get("log2fc") is None else float(row["log2fc"])
    finite = log2fc is not None and math.isfinite(log2fc)
    return {
        "gene": name,
        "log2fc": log2fc if finite else None,
        "found_in_one_group_only": log2fc is not None and math.isinf(log2fc),
        "likely_found_in_one_group_only": finite
        and abs(log2fc or 0) > OFF_SCALE_LOG2FC,
        "fdr": row["fdr"],
        "pct_1": row.get("pct_1"),
        "pct_2": row.get("pct_2"),
    }


def stored_markers(
    reads: Reads, dataset_id: int, cluster_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Each cell type's stored markers ({top, n_significant}), as the panel lists them."""
    rows = reads.rows_in(
        "scrna_cluster_stats",
        "cluster_id",
        cluster_ids,
        (Filter("dataset_id", "eq", dataset_id),),
    )
    return {r["cluster_id"]: r["markers"] for r in rows if r.get("markers")}
