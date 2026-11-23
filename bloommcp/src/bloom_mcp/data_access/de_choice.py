"""Which DE runs and comparisons count, chosen as Bloom's DE pages choose them; shared by
the gene lookup and the DE tool."""

from __future__ import annotations

from typing import Any

from . import gene_reads
from .gene_reads import Reads
from .table_ports import Filter, Sort

# What a one-vs-rest comparison's other side is called (REST_GROUP in cluster-markers.ts).
REST_GROUP = "rest"


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
    first, so a cut at MAX_ROWS drops the oldest runs; either cut marks it incomplete.
    """
    if not run_ids:
        return []
    if len(run_ids) > gene_reads.IN_CHUNK:
        reads.incomplete.add("scrna_de_runs")
    found, more = reads.page(
        "scrna_de",
        [Filter("run_id", "in", run_ids[: gene_reads.IN_CHUNK]), *filters],
        [Sort("run_id", descending=True), Sort("id")],
        limit=gene_reads.MAX_ROWS,
    )
    if more:
        reads.incomplete.add("scrna_de")
    return found


def read_one_vs_rest(
    reads: Reads, run_ids: list[int], cluster_ids: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Each cell type's tested one-vs-rest comparison, chosen as findOneVsRest chooses;
    every cell type's when `cluster_ids` is None. Callers keep the list short: it goes
    into the URL whole."""
    if cluster_ids == []:
        return {}
    filters = [
        Filter("group_kind", "eq", "cluster"),
        Filter("group2", "eq", REST_GROUP),
        Filter("tested", "eq", True),
    ]
    if cluster_ids is not None:
        filters.append(Filter("cluster_id", "in", cluster_ids))
    found = comparisons(reads, run_ids, filters)
    own = [c for c in found if c["group1"] == c["cluster_id"]]
    return pick_one_vs_rest(own, run_ids)


def pick_one_vs_rest(
    rows: list[dict[str, Any]], runs: list[int]
) -> dict[str, dict[str, Any]]:
    """Per cluster, its comparison against the rest in the newest run that has one; with
    several (one per library), the one over the most cells (findOneVsRest)."""
    chosen: dict[str, dict[str, Any]] = {}
    for cluster in {c["cluster_id"] for c in rows}:
        own = [c for c in rows if c["cluster_id"] == cluster]
        newest = next(r for r in runs if any(c["run_id"] == r for c in own))
        chosen[cluster] = max(
            (c for c in own if c["run_id"] == newest),
            key=lambda c: (cells(c), -c["id"]),
        )
    return chosen


def pick_genotype(rows: list[dict[str, Any]], runs: list[int]) -> list[dict[str, Any]]:
    """Every run's genotype comparisons, the newest run winning for each cluster and
    contrast, as the page combines them (fetchAllComparisons)."""
    rank = {run: i for i, run in enumerate(runs)}
    seen: set[tuple[Any, Any]] = set()
    kept = []
    for c in sorted(rows, key=lambda c: (rank.get(c["run_id"], len(rank)), c["id"])):
        key = (c.get("cluster_id"), c.get("contrast"))
        if key not in seen:
            seen.add(key)
            kept.append(c)
    return kept
