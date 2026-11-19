"""The DE tool's reads: a dataset's cell types, complete runs and chosen comparisons; each
cell type's markers as the cluster panel shows them; and each comparison's genes passing
the DE tab's cuts."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from . import de_choice, gene_reads
from .gene_reads import (
    DE_FDR_CUT,
    DE_LOG2FC_CUT,
    MARKER_FDR_CUT,
    OFF_SCALE_LOG2FC,
    Reads,
)
from .table_ports import Filter, Sort

# Most cell types one call returns.
MAX_CELL_TYPES = 10

Direction = Literal["up", "down"]


def live_dataset(reads: Reads, dataset_id: int) -> dict[str, Any] | None:
    """The dataset, or None when it is missing or deleted."""
    found = reads.rows(
        "scrna_datasets",
        [Filter("id", "eq", dataset_id), Filter("deleted_at", "is_null", True)],
    )
    return found[0] if found else None


@dataclass
class CellTypes:
    """The cell types a call covers, in the website's order, and what it couldn't."""

    chosen: list[dict[str, Any]] = field(default_factory=list)
    left_out: list[dict[str, Any]] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)


def cell_types(reads: Reads, dataset_id: int, wanted: list[str]) -> CellTypes:
    """Cell types matching a cluster id or name in any case (each cluster carrying a
    matched name), or all of them when none are named; at most MAX_CELL_TYPES."""
    clusters = reads.rows("scrna_clusters", [Filter("dataset_id", "eq", dataset_id)])
    clusters.sort(key=lambda c: (c.get("ordinal") is None, c.get("ordinal"), c["id"]))
    found = CellTypes()
    keys = list(dict.fromkeys(k for w in wanted if (k := w.strip().casefold())))
    if keys:
        given = {w.strip().casefold(): w.strip() for w in reversed(wanted)}
        found.unknown = [
            given[k] for k in keys if not any(_named(c, k) for c in clusters)
        ]
        clusters = [c for c in clusters if any(_named(c, k) for k in keys)]
    found.chosen = clusters[:MAX_CELL_TYPES]
    found.left_out = clusters[MAX_CELL_TYPES:]
    return found


def _named(cluster: dict[str, Any], key: str) -> bool:
    names = (cluster["cluster_id"], cluster.get("name"))
    return any(n is not None and n.strip().casefold() == key for n in names)


def runs(reads: Reads, dataset_id: int) -> list[dict[str, Any]]:
    """The dataset's complete DE runs, newest first, with their method."""
    return de_choice.complete_runs(reads, [dataset_id]).get(dataset_id, [])


def is_tested(comparison: dict[str, Any]) -> bool:
    """The website's isTested: not marked untested, and some genes tested."""
    return (
        comparison.get("tested") is not False and comparison.get("n_genes_tested") != 0
    )


def one_vs_rest(
    reads: Reads, run_ids: list[int], cluster_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Each cell type's tested one-vs-rest comparison, chosen as findOneVsRest chooses."""
    if not cluster_ids:
        return {}
    found = de_choice.comparisons(
        reads,
        run_ids,
        [
            Filter("group_kind", "eq", "cluster"),
            Filter("group2", "eq", de_choice.REST_GROUP),
            Filter("tested", "eq", True),
            Filter("cluster_id", "in", cluster_ids),
        ],
    )
    own = [c for c in found if c["group1"] == c["cluster_id"]]
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
    scopes = [Filter("cluster_id", "in", cluster_ids)] if cluster_ids else []
    if whole:
        scopes.append(Filter("cluster_id", "is_null", True))
    found = [
        c
        for scope in scopes
        for c in de_choice.comparisons(reads, run_ids, [*kind, scope])
    ]
    return de_choice.genotype(found, run_ids)


def panel_markers(
    reads: Reads, dataset_id: int, cluster_ids: list[str], run_ids: list[int]
) -> dict[str, dict[str, Any]]:
    """Each cell type's markers as the cluster panel shows them: the stored markers, or
    without them the top of its one-vs-rest comparison. Cell types with neither are left
    out."""
    rows = reads.rows_in(
        "scrna_cluster_stats",
        "cluster_id",
        cluster_ids,
        (Filter("dataset_id", "eq", dataset_id),),
    )
    found = {}
    for row in rows:
        stored = _parse_markers(row.get("markers"))
        if stored and stored["top"]:
            found[row["cluster_id"]] = {"from": "stored", **stored}
    missing = [c for c in cluster_ids if c not in found]
    for cluster, comparison in one_vs_rest(reads, run_ids, missing).items():
        found[cluster] = {
            "from": "comparison",
            **_comparison_markers(reads, comparison),
        }
    return found


def _parse_markers(raw: Any) -> dict[str, Any] | None:
    """The stored markers cleaned as parseMarkers cleans them; q is the FDR."""
    if not isinstance(raw, dict) or not isinstance(raw.get("top"), list):
        return None

    def number(entry: dict[str, Any], key: str, default: float) -> float:
        value = entry.get(key)
        return value if isinstance(value, (int, float)) else default

    top = [
        {
            "gene": m["gene"],
            "symbol": m.get("symbol") or None,
            "log2fc": number(m, "log2fc", 0),
            "fdr": number(m, "q", 1),
            "pct_1": number(m, "pct_1", 0),
            "pct_2": number(m, "pct_2", 0),
            "source": m.get("source") or None,
        }
        for m in raw["top"]
        if isinstance(m, dict) and isinstance(m.get("gene"), str)
    ]
    n = raw.get("n_significant")
    return {"top": top, "n_significant": n if isinstance(n, int) else 0}


def _comparison_markers(reads: Reads, comparison: dict[str, Any]) -> dict[str, Any]:
    """The panel's fallback (fetchDeMarkers): higher in the cluster and below the FDR cut,
    ranked by FDR then log2FC."""
    filters = [
        Filter("de_id", "eq", comparison["id"]),
        Filter("log2fc", "gt", 0),
        Filter("fdr", "lt", MARKER_FDR_CUT),
    ]
    order = [Sort("fdr"), Sort("log2fc", descending=True), Sort("id")]
    rows, more = reads.page(
        "scrna_de_genes", filters, order, limit=de_choice.TOP_MARKERS
    )
    names = _gene_names(reads, [r["gene_id"] for r in rows])
    return {
        "top": [_gene(r, names.get(r["gene_id"])) for r in rows],
        "n_significant": _count(reads, filters) if more else len(rows),
    }


def de_genes(
    reads: Reads, de_id: int, top: int
) -> dict[Direction, tuple[list[dict[str, Any]], int | None]]:
    """The comparison's `top` genes up and down passing the DE tab's cuts, ranked as the
    website's summary ranks them (FDR, then gene name, then gene id), and how many pass
    in each direction (None when the count is unknown)."""
    top = max(0, min(top, gene_reads.MAX_ROWS))
    found = {d: _passing(reads, de_id, d, top) for d in ("up", "down")}
    names = _gene_names(
        reads, [r["gene_id"] for rows, _, _ in found.values() for r in rows]
    )
    result: dict[Direction, tuple[list[dict[str, Any]], int | None]] = {}
    for direction, (rows, more, filters) in found.items():
        ranked = sorted(rows, key=lambda r: _rank(r, names))[:top]
        count = _count(reads, filters) if more else len(rows)
        result[direction] = ([_gene(r, names.get(r["gene_id"])) for r in ranked], count)
    return result


def _passing(
    reads: Reads, de_id: int, direction: Direction, top: int
) -> tuple[list[dict[str, Any]], bool, list[Filter]]:
    """The first `top` passing rows by FDR, with every row tied at the cut (the website
    breaks FDR ties by name, not row id); whether more pass; and the filters."""
    side = (
        [Filter("log2fc", "gt", DE_LOG2FC_CUT), Filter("log2fc", "neq", "NaN")]
        if direction == "up"
        else [Filter("log2fc", "lt", -DE_LOG2FC_CUT)]
    )
    filters = [Filter("de_id", "eq", de_id), Filter("fdr", "lt", DE_FDR_CUT), *side]
    rows, more = reads.page("scrna_de_genes", filters, [Sort("fdr"), Sort("id")], top)
    if more and rows:
        tied = reads.rows(
            "scrna_de_genes", [*filters, Filter("fdr", "eq", rows[-1]["fdr"])]
        )
        rows = list({r["id"]: r for r in [*rows, *tied]}.values())
    return rows, more, filters


def _rank(row: dict[str, Any], names: dict[int, str]) -> tuple[Any, ...]:
    # Case-folded first, as localeCompare compares letters before case.
    name = names.get(row["gene_id"]) or ""
    return (row["fdr"], name.casefold(), name, row["gene_id"])


def _count(reads: Reads, filters: list[Filter]) -> int | None:
    return reads.reader.count("scrna_de_genes", "id", filters, exact=True)


def _gene_names(reads: Reads, gene_ids: list[int]) -> dict[int, str]:
    rows = reads.rows_in("scrna_genes", "id", gene_ids)
    return {r["id"]: r["gene_name"] for r in rows}


def _number(value: Any) -> float | None:
    """A result column as a finite number, else None (JSON has no NaN or infinity)."""
    number = None if value is None else float(value)
    return number if number is not None and math.isfinite(number) else None


def _gene(row: dict[str, Any], name: str | None) -> dict[str, Any]:
    raw = None if row.get("log2fc") is None else float(row["log2fc"])
    log2fc = _number(raw)
    return {
        "gene": name,
        "log2fc": log2fc,
        "found_in_one_group_only": raw is not None and math.isinf(raw),
        "likely_found_in_one_group_only": log2fc is not None
        and abs(log2fc) > OFF_SCALE_LOG2FC,
        "fdr": row["fdr"],
        "pct_1": _number(row.get("pct_1")),
        "pct_2": _number(row.get("pct_2")),
    }
