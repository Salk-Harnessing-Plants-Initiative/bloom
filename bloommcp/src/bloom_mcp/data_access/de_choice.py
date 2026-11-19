"""Which DE runs and comparisons count, chosen as Bloom's DE pages choose them; shared by
the gene lookup and the DE tool."""

from __future__ import annotations

from typing import Any

from . import gene_reads
from .gene_reads import Reads
from .table_ports import Filter, Sort

# What a one-vs-rest comparison's other side is called (REST_GROUP in cluster-markers.ts).
REST_GROUP = "rest"
# How many markers the cluster panel lists (TOP_MARKERS in cluster-markers.ts).
TOP_MARKERS = 25


def cells(row: dict[str, Any]) -> int:
    return (row.get("n_group1") or 0) + (row.get("n_group2") or 0)


def newest_first(reads: Reads, dataset_ids: list[int]) -> dict[int, list[int]]:
    """Each dataset's complete DE run ids, newest first."""
    return {
        dataset: [r["id"] for r in runs]
        for dataset, runs in complete_runs(reads, dataset_ids).items()
    }


def complete_runs(
    reads: Reads, dataset_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    """Each dataset's complete DE runs, newest first."""
    runs = reads.rows_in(
        "scrna_de_runs",
        "dataset_id",
        dataset_ids,
        (Filter("status", "eq", "complete"),),
    )
    runs.sort(key=lambda r: (r.get("completed_at") or "", r["id"]), reverse=True)
    by_dataset: dict[int, list[dict[str, Any]]] = {}
    for run in runs:
        by_dataset.setdefault(run["dataset_id"], []).append(run)
    return by_dataset


def comparisons(
    reads: Reads, run_ids: list[int], filters: list[Filter]
) -> list[dict[str, Any]]:
    """Comparisons in the newest IN_CHUNK of `run_ids` matching `filters`, newest run
    first, so a cut at MAX_ROWS drops the oldest runs."""
    if not run_ids:
        return []
    found, more = reads.page(
        "scrna_de",
        [Filter("run_id", "in", run_ids[: gene_reads.IN_CHUNK]), *filters],
        [Sort("run_id", descending=True), Sort("id")],
        limit=gene_reads.MAX_ROWS,
    )
    if more:
        reads.incomplete.add("scrna_de")
    return found


def one_vs_rest(
    comparisons: list[dict[str, Any]], runs: list[int]
) -> dict[str, dict[str, Any]]:
    """Per cluster, its comparison against the rest in the newest run that has one; with
    several (one per library), the one over the most cells (findOneVsRest)."""
    chosen: dict[str, dict[str, Any]] = {}
    for cluster in {c["cluster_id"] for c in comparisons}:
        own = [c for c in comparisons if c["cluster_id"] == cluster]
        newest = next(r for r in runs if any(c["run_id"] == r for c in own))
        chosen[cluster] = max(
            (c for c in own if c["run_id"] == newest),
            key=lambda c: (cells(c), -c["id"]),
        )
    return chosen


def genotype(
    comparisons: list[dict[str, Any]], runs: list[int]
) -> list[dict[str, Any]]:
    """Every run's genotype comparisons, the newest run winning for each cluster and
    contrast, as the page combines them (fetchAllComparisons)."""
    rank = {run: i for i, run in enumerate(runs)}
    seen: set[tuple[Any, Any]] = set()
    kept = []
    for c in sorted(
        comparisons, key=lambda c: (rank.get(c["run_id"], len(rank)), c["id"])
    ):
        key = (c.get("cluster_id"), c.get("contrast"))
        if key not in seen:
            seen.add(key)
            kept.append(c)
    return kept
