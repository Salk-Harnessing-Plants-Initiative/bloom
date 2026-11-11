"""The gene lookup's expression reads: which scRNA datasets measured a gene, where it marks a
cluster, and which genotype comparisons it passes, each picked as Bloom's pages pick them."""

from __future__ import annotations

import math
from typing import Any

from . import gene_reads
from .gene_reads import (
    DE_FDR_CUT,
    DE_LOG2FC_CUT,
    IN_CHUNK,
    MARKER_FDR_CUT,
    OFF_SCALE_LOG2FC,
    Reads,
)
from .table_ports import Filter, Sort

# What a one-vs-rest comparison's other side is called (REST_GROUP in cluster-markers.ts).
REST_GROUP = "rest"
# How many markers a cluster's panel lists (TOP_MARKERS in cluster-markers.ts).
TOP_MARKERS = 25


# Result columns PostgREST may send as text: Postgres's NaN and Infinity arrive as strings.
_NUMBERS = ("log2fc", "fdr", "pct_1", "pct_2")


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


def _finite(value: float | None) -> float | None:
    """`value`, or None when it's NaN or infinite (JSON has no such numbers)."""
    return value if value is not None and math.isfinite(value) else None


def _absent(log2fc: float | None) -> bool:
    return log2fc is not None and abs(log2fc) > OFF_SCALE_LOG2FC


def _cells(row: dict[str, Any]) -> int:
    return (row.get("n_group1") or 0) + (row.get("n_group2") or 0)


def datasets(
    reads: Reads, names: list[str], construct_terms: list[str]
) -> list[dict[str, Any]]:
    """Live datasets that measured one of `names`, one entry per gene measured, or whose
    genotype construct names the gene.

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
        by_name: dict[str | None, list[int]] = {}
        for g in own:
            by_name.setdefault(g["gene_name"], []).append(g["id"])
        for gene_name, row_ids in sorted(by_name.items()) or [(None, [])]:
            result.append(
                {
                    **dataset,
                    "gene_name": gene_name,
                    "gene_row_ids": row_ids,
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
    return {r["de_id"]: {**r, **{k: _number(r.get(k)) for k in _NUMBERS}} for r in rows}


def _comparisons(
    reads: Reads, run_ids: list[int], kind: tuple[Filter, ...]
) -> list[dict[str, Any]]:
    """One dataset's tested comparisons of one kind, newest run first, so a cut at
    MAX_ROWS drops the oldest runs."""
    if not run_ids:
        return []
    found, more = reads.page(
        "scrna_de",
        [
            Filter("run_id", "in", run_ids[:IN_CHUNK]),
            Filter("tested", "eq", True),
            *kind,
        ],
        [Sort("run_id", descending=True), Sort("id")],
        limit=gene_reads.MAX_ROWS,
    )
    if more:
        reads.incomplete.add("scrna_de")
    return found


_ONE_VS_REST = (
    Filter("group_kind", "eq", "cluster"),
    Filter("group2", "eq", REST_GROUP),
)
_GENOTYPE = (Filter("group_kind", "eq", "genotype"),)


def add_results(reads: Reads, found: list[dict[str, Any]]) -> None:
    """Fill each dataset's `markers` and `genotype_comparisons` in place."""
    measured = [d for d in found if d["gene_row_ids"]]
    for dataset in found:
        dataset["markers"], dataset["genotype_comparisons"] = [], []
    runs = _newest_first(reads, [d["id"] for d in measured])
    for dataset in measured:
        own_runs = runs.get(dataset["id"], [])
        rest = [
            c
            for c in _comparisons(reads, own_runs, _ONE_VS_REST)
            if c["group1"] == c["cluster_id"]
        ]
        markers = _one_vs_rest(rest, own_runs)
        genotypes = _genotype(_comparisons(reads, own_runs, _GENOTYPE), own_runs)
        results = _de_rows(
            reads,
            [c["id"] for c in [*markers.values(), *genotypes]],
            dataset["gene_row_ids"],
        )
        hits = _marker_hits(markers, results)
        passed = [
            c
            for c in genotypes
            if c["id"] in results and _passes_de_cut(results[c["id"]])
        ]
        labels = _labels(
            reads, dataset["id"], [*hits, *(c["cluster_id"] for c in passed)]
        )
        panel = _on_panel(reads, dataset, markers, hits)
        dataset["markers"] = _markers(hits, labels, panel)
        dataset["genotype_comparisons"] = [
            _genotype_hit(c, results[c["id"]], labels) for c in passed
        ]


def _marker_hits(
    chosen: dict[str, dict[str, Any]], results: dict[int, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """The gene's result in each cluster it is up in, past the marker cut."""
    return {
        cluster: results[c["id"]]
        for cluster, c in chosen.items()
        if c["id"] in results
        and (results[c["id"]].get("log2fc") or 0) > 0
        and results[c["id"]]["fdr"] < MARKER_FDR_CUT
    }


def _labels(
    reads: Reads, dataset_id: int, clusters: list[str | None]
) -> dict[str, dict[str, Any]]:
    """Each cluster's cell-type label row, by cluster id."""
    rows = reads.rows_in(
        "scrna_clusters",
        "cluster_id",
        [c for c in clusters if c is not None],
        (Filter("dataset_id", "eq", dataset_id),),
    )
    return {row["cluster_id"]: row for row in rows}


def _on_panel(
    reads: Reads,
    dataset: dict[str, Any],
    chosen: dict[str, dict[str, Any]],
    hits: dict[str, dict[str, Any]],
) -> dict[str, bool]:
    """Whether each marker cluster's panel lists the gene: the panel shows the cluster's
    saved top markers, or without them the top of its one-vs-rest comparison."""
    if not hits:
        return {}
    stats = reads.rows_in(
        "scrna_cluster_stats",
        "cluster_id",
        list(hits),
        (Filter("dataset_id", "eq", dataset["id"]),),
    )
    saved = {r["cluster_id"]: (r.get("markers") or {}).get("top") or [] for r in stats}
    name = (dataset.get("gene_name") or "").lower()
    panel = {}
    for cluster in hits:
        if saved.get(cluster):
            panel[cluster] = any(
                str(m.get("gene", "")).lower() == name for m in saved[cluster]
            )
            continue
        top, _ = reads.page(
            "scrna_de_genes",
            [
                Filter("de_id", "eq", chosen[cluster]["id"]),
                Filter("log2fc", "gt", 0),
                Filter("fdr", "lt", MARKER_FDR_CUT),
            ],
            [Sort("fdr"), Sort("log2fc", descending=True)],
            limit=TOP_MARKERS,
        )
        panel[cluster] = any(r["gene_id"] in dataset["gene_row_ids"] for r in top)
    return panel


def _markers(
    hits: dict[str, dict[str, Any]],
    labels: dict[str, dict[str, Any]],
    panel: dict[str, bool],
) -> list[dict[str, Any]]:
    return [
        {
            "cluster_id": cluster,
            "cell_type": labels.get(cluster, {}).get("name"),
            "label_source": labels.get(cluster, {}).get("source"),
            "log2fc": _finite(hit["log2fc"]),
            "absent_in_one_group": _absent(hit["log2fc"]),
            "fdr": hit["fdr"],
            "pct_in_cluster": hit.get("pct_1"),
            "pct_elsewhere": hit.get("pct_2"),
            "on_cluster_panel": panel.get(cluster, False),
        }
        for cluster, hit in sorted(hits.items(), key=lambda kv: kv[1]["fdr"])
    ]


def _passes_de_cut(row: dict[str, Any]) -> bool:
    return row["fdr"] < DE_FDR_CUT and abs(row.get("log2fc") or 0) > DE_LOG2FC_CUT


def _genotype_hit(
    comparison: dict[str, Any],
    row: dict[str, Any],
    labels: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    log2fc = row.get("log2fc") or 0
    cluster = comparison.get("cluster_id")
    return {
        "cluster_id": cluster,
        "cell_type": labels.get(cluster, {}).get("name") if cluster else None,
        "group1": comparison["group1"],
        "group2": comparison["group2"],
        "log2fc": _finite(log2fc),
        "fdr": row["fdr"],
        "direction": "up" if log2fc > 0 else "down",
        "absent_in_one_group": _absent(log2fc),
    }
