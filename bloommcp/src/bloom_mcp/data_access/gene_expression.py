"""The gene lookup's expression reads: which scRNA datasets measured a gene, where it marks a
cluster, and which genotype comparisons it passes, each picked as Bloom's pages pick them."""

from __future__ import annotations

from typing import Any

from .gene_reads import (
    DE_FDR_CUT,
    DE_LOG2FC_CUT,
    MARKER_FDR_CUT,
    OFF_SCALE_LOG2FC,
    Reads,
)
from .table_ports import Filter

# What a one-vs-rest comparison's other side is called (REST_GROUP in cluster-markers.ts).
REST_GROUP = "rest"


def _cells(row: dict[str, Any]) -> int:
    return (row.get("n_group1") or 0) + (row.get("n_group2") or 0)


def datasets(
    reads: Reads, names: list[str], construct_terms: list[str]
) -> list[dict[str, Any]]:
    """Live datasets that measured one of `names`, or whose genotype construct names the gene.

    bloom_agent reads no deleted dataset, so a gene row whose dataset isn't read is dropped.
    """
    genes = reads.rows_in("scrna_genes", "gene_name", names)
    constructs = [
        row
        for term in construct_terms
        for row in reads.rows(
            "scrna_genotypes", [Filter("construct", "contains", term)]
        )
    ]
    ids = [g["dataset_id"] for g in genes] + [c["dataset_id"] for c in constructs]
    found = reads.rows_in(
        "scrna_datasets", "id", ids, (Filter("deleted_at", "is_null", True),)
    )
    result = []
    for dataset in sorted(found, key=lambda d: d["id"]):
        own = [g for g in genes if g["dataset_id"] == dataset["id"]]
        named = {c["construct"] for c in constructs if c["dataset_id"] == dataset["id"]}
        result.append(
            {
                **dataset,
                "gene_name": own[0]["gene_name"] if own else None,
                "gene_row_ids": [g["id"] for g in own],
                "constructs": sorted(named),
            }
        )
    return result


def _newest_first(reads: Reads, dataset_ids: list[int]) -> dict[int, list[int]]:
    """Each dataset's complete DE runs, newest first."""
    runs = reads.rows_in(
        "scrna_de_runs",
        "dataset_id",
        dataset_ids,
        (Filter("status", "eq", "complete"),),
    )
    runs.sort(key=lambda r: (r.get("completed_at") or "", r["id"]), reverse=True)
    by_dataset: dict[int, list[int]] = {}
    for run in runs:
        by_dataset.setdefault(run["dataset_id"], []).append(run["id"])
    return by_dataset


def _one_vs_rest(
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
            key=lambda c: (_cells(c), -c["id"]),
        )
    return chosen


def _genotype(
    comparisons: list[dict[str, Any]], runs: list[int]
) -> list[dict[str, Any]]:
    """The genotype comparisons of the newest run that has any."""
    newest = next((r for r in runs if any(c["run_id"] == r for c in comparisons)), None)
    return [c for c in comparisons if c["run_id"] == newest]


def _de_rows(
    reads: Reads, comparison_ids: list[int], gene_row_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """The gene's result in each comparison, by comparison id."""
    if not comparison_ids or not gene_row_ids:
        return {}
    rows = reads.rows_in(
        "scrna_de_genes",
        "de_id",
        comparison_ids,
        (Filter("gene_id", "in", gene_row_ids),),
    )
    return {r["de_id"]: r for r in rows}


def add_results(reads: Reads, found: list[dict[str, Any]]) -> None:
    """Fill each dataset's `markers` and `genotype_comparisons` in place."""
    measured = [d for d in found if d["gene_row_ids"]]
    for dataset in found:
        dataset["markers"], dataset["genotype_comparisons"] = [], []
    runs = _newest_first(reads, [d["id"] for d in measured])
    run_ids = [r for ids in runs.values() for r in ids]
    comparisons = reads.rows_in(
        "scrna_de", "run_id", run_ids, (Filter("tested", "eq", True),)
    )
    for dataset in measured:
        own_runs = runs.get(dataset["id"], [])
        own = [c for c in comparisons if c["dataset_id"] == dataset["id"]]
        rest = [
            c
            for c in own
            if c["group_kind"] == "cluster"
            and c["group2"] == REST_GROUP
            and c["group1"] == c["cluster_id"]
        ]
        markers = _one_vs_rest(rest, own_runs)
        genotypes = _genotype(
            [c for c in own if c["group_kind"] == "genotype"], own_runs
        )
        results = _de_rows(
            reads,
            [c["id"] for c in [*markers.values(), *genotypes]],
            dataset["gene_row_ids"],
        )
        dataset["markers"] = _markers(reads, dataset["id"], markers, results)
        dataset["genotype_comparisons"] = [
            _genotype_hit(c, results[c["id"]])
            for c in genotypes
            if c["id"] in results and _passes_de_cut(results[c["id"]])
        ]


def _markers(
    reads: Reads,
    dataset_id: int,
    chosen: dict[str, dict[str, Any]],
    results: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    hits = {
        cluster: results[c["id"]]
        for cluster, c in chosen.items()
        if c["id"] in results
        and (results[c["id"]].get("log2fc") or 0) > 0
        and results[c["id"]]["fdr"] < MARKER_FDR_CUT
    }
    if not hits:
        return []
    labels = reads.rows_in(
        "scrna_clusters",
        "cluster_id",
        list(hits),
        (Filter("dataset_id", "eq", dataset_id),),
    )
    by_cluster = {row["cluster_id"]: row for row in labels}
    return [
        {
            "cluster_id": cluster,
            "cell_type": by_cluster.get(cluster, {}).get("name"),
            "label_source": by_cluster.get(cluster, {}).get("source"),
            "log2fc": hit["log2fc"],
            "fdr": hit["fdr"],
            "pct_in_cluster": hit.get("pct_1"),
            "pct_elsewhere": hit.get("pct_2"),
        }
        for cluster, hit in sorted(hits.items(), key=lambda kv: kv[1]["fdr"])
    ]


def _passes_de_cut(row: dict[str, Any]) -> bool:
    return row["fdr"] < DE_FDR_CUT and abs(row.get("log2fc") or 0) > DE_LOG2FC_CUT


def _genotype_hit(comparison: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    log2fc = row.get("log2fc") or 0
    return {
        "group1": comparison["group1"],
        "group2": comparison["group2"],
        "log2fc": log2fc,
        "fdr": row["fdr"],
        "direction": "up" if log2fc > 0 else "down",
        "absent_in_one_group": abs(log2fc) > OFF_SCALE_LOG2FC,
    }
