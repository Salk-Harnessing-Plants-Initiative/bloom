"""A cell type's markers as the website's cluster panel lists them (cluster-markers.ts);
shared by the gene lookup and the DE tool."""

from __future__ import annotations

from typing import Any

from .gene_reads import MARKER_FDR_CUT, Reads
from .table_ports import Filter, Sort

# How many markers the cluster panel lists (TOP_MARKERS in cluster-markers.ts).
TOP_MARKERS = 25


def _is_number(value: Any) -> bool:
    # JS's typeof: a boolean is not a number.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_markers(raw: Any) -> dict[str, Any] | None:
    """Stored markers cleaned as parseMarkers cleans them, with q renamed fdr; None when
    they aren't a list."""
    if not isinstance(raw, dict) or not isinstance(raw.get("top"), list):
        return None

    def number(entry: dict[str, Any], key: str, default: float) -> float:
        value = entry.get(key)
        return value if _is_number(value) else default

    def text(entry: dict[str, Any], key: str) -> str | None:
        value = entry.get(key)
        return value if isinstance(value, str) and value else None

    top = [
        {
            "gene": m["gene"],
            "symbol": text(m, "symbol"),
            "log2fc": number(m, "log2fc", 0),
            "fdr": number(m, "q", 1),
            "pct_1": number(m, "pct_1", 0),
            "pct_2": number(m, "pct_2", 0),
            "source": text(m, "source"),
        }
        for m in raw["top"]
        if isinstance(m, dict) and isinstance(m.get("gene"), str)
    ]
    n = raw.get("n_significant")
    return {"top": top, "n_significant": n if _is_number(n) else 0}


def stored(
    reads: Reads, dataset_id: int, cluster_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Each cell type's stored markers the panel shows: a parsed, non-empty list."""
    rows = reads.rows_in(
        "scrna_cluster_stats",
        "cluster_id",
        cluster_ids,
        (Filter("dataset_id", "eq", dataset_id),),
    )
    found = {}
    for row in rows:
        parsed = parse_markers(row.get("markers"))
        if parsed and parsed["top"]:
            found[row["cluster_id"]] = parsed
    return found


def fallback_filters(de_id: int) -> list[Filter]:
    """The panel's fallback cuts (fetchDeMarkers): higher in the cluster, below the FDR cut."""
    return [
        Filter("de_id", "eq", de_id),
        Filter("log2fc", "gt", 0),
        Filter("fdr", "lt", MARKER_FDR_CUT),
    ]


def fallback(reads: Reads, de_id: int) -> tuple[list[dict[str, Any]], bool]:
    """The top TOP_MARKERS genes of a one-vs-rest comparison passing the fallback cuts,
    ranked by FDR then log2FC, and whether more pass."""
    order = [Sort("fdr"), Sort("log2fc", descending=True), Sort("id")]
    return reads.page("scrna_de_genes", fallback_filters(de_id), order, TOP_MARKERS)
