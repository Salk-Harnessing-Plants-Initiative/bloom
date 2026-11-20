"""The DE tool's reads: a dataset's cell types, complete runs and chosen comparisons; each
cell type's markers as the cluster panel shows them; and each comparison's genes passing
the DE tab's cuts."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any, Literal

from . import cluster_panel, de_choice, gene_reads
from .gene_reads import DE_FDR_CUT, DE_LOG2FC_CUT, OFF_SCALE_LOG2FC, Reads
from .table_ports import Filter, Sort

# Most cell types one call returns.
MAX_CELL_TYPES = 10

Direction = Literal["up", "down"]
DIRECTIONS: tuple[Direction, ...] = ("up", "down")

# ICU's root order of printable ASCII, as localeCompare sorts it; letters ignore case here.
_ICU_ORDER = " _-,;:!?.'\"()[]{}@*/\\&#%`^+<=>|~$0123456789abcdefghijklmnopqrstuvwxyz"
_ICU_RANK = {c: i for i, c in enumerate(_ICU_ORDER)}


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


def genotype(
    reads: Reads,
    run_ids: list[int],
    cluster_ids: list[str],
    contrast: str | None,
    *,
    whole: bool,
) -> list[dict[str, Any]]:
    """Genotype comparisons of `cluster_ids` (and the whole dataset when `whole`), tested
    or not, the newest run winning per cell type and contrast (fetchAllComparisons).
    Callers keep `cluster_ids` short: it goes into the URL whole."""
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
    return de_choice.pick_genotype(found, run_ids)


def panel_markers(
    reads: Reads,
    dataset_id: int,
    cluster_ids: list[str],
    run_ids: list[int],
    late: Callable[[], bool] = lambda: False,
) -> dict[str, dict[str, Any]]:
    """Each cell type's markers as the cluster panel shows them: the stored markers, or
    without them the top of its one-vs-rest comparison from `run_ids` (every complete run,
    newest first). Cell types with neither are left out; once `late()`, the rest are
    "not_read"."""
    found = {
        cluster: {
            "from": "stored",
            "top": [
                _result(m, m["gene"], m["symbol"], m["source"]) for m in parsed["top"]
            ],
            "n_significant": parsed["n_significant"],
        }
        for cluster, parsed in cluster_panel.stored(
            reads, dataset_id, cluster_ids
        ).items()
    }
    missing = [c for c in cluster_ids if c not in found]
    for cluster, comparison in de_choice.read_one_vs_rest(
        reads, run_ids, missing
    ).items():
        if late():
            found[cluster] = {"from": "not_read", "top": [], "n_significant": None}
            continue
        rows, more = cluster_panel.fallback(reads, comparison["id"])
        names = _gene_names(reads, [r["gene_id"] for r in rows])
        count_filters = cluster_panel.fallback_filters(comparison["id"])
        found[cluster] = {
            "from": "comparison",
            "top": [_result(r, names.get(r["gene_id"])) for r in rows],
            "n_significant": _count(reads, count_filters) if more else len(rows),
        }
    return found


def de_genes(
    reads: Reads, comparison: dict[str, Any], top: int
) -> dict[Direction, tuple[list[dict[str, Any]], int | None]] | None:
    """The comparison's `top` genes up and down passing the DE tab's cuts, ranked as the
    website's summary ranks them (FDR, then gene name, then gene id), and how many pass
    in each direction (None when the count is unknown). None when the comparison wasn't
    tested: the website shows "not tested", not "no genes"."""
    if not is_tested(comparison):
        return None
    top = max(0, min(top, gene_reads.MAX_ROWS))
    found = {d: _passing(reads, comparison["id"], d, top) for d in DIRECTIONS}
    names = _gene_names(
        reads, [r["gene_id"] for rows, _, _ in found.values() for r in rows]
    )
    result: dict[Direction, tuple[list[dict[str, Any]], int | None]] = {}
    for direction, (rows, more, filters) in found.items():
        ranked = sorted(rows, key=lambda r: _rank(r, names))[:top]
        count = _count(reads, filters) if more else len(rows)
        result[direction] = (
            [_result(r, names.get(r["gene_id"])) for r in ranked],
            count,
        )
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
    name = names.get(row["gene_id"]) or ""
    return (row["fdr"], name_order(name), name, row["gene_id"])


def name_order(name: str) -> tuple[Any, ...]:
    """A sort key ordering names as the website's localeCompare does (ICU root): letters,
    then accents, then case with lower case first."""
    letters: list[int] = []
    accents: list[tuple[str, ...]] = []
    cases: list[int] = []
    for ch in unicodedata.normalize("NFD", name):
        if unicodedata.combining(ch) and accents:
            accents[-1] += (ch,)
            continue
        low = ch.lower()
        letters.append(_ICU_RANK.get(low, len(_ICU_ORDER) + ord(low)))
        accents.append(())
        cases.append(0 if ch == low else 1)
    return (letters, accents, cases)


def _count(reads: Reads, filters: list[Filter]) -> int | None:
    return reads.reader.count("scrna_de_genes", "id", filters, exact=True)


def _gene_names(reads: Reads, gene_ids: list[int]) -> dict[int, str]:
    rows = reads.rows_in("scrna_genes", "id", gene_ids)
    return {r["id"]: r["gene_name"] for r in rows}


def _number(value: Any) -> float | None:
    """A result column as a finite number, else None (JSON has no NaN or infinity)."""
    number = None if value is None else float(value)
    return number if number is not None and math.isfinite(number) else None


def _result(
    row: dict[str, Any],
    gene: str | None,
    symbol: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """One gene's result, the same shape for stored markers, computed markers and DE."""
    raw = None if row.get("log2fc") is None else float(row["log2fc"])
    log2fc = _number(raw)
    return {
        "gene": gene,
        "symbol": symbol,
        "log2fc": log2fc,
        "found_in_one_group_only": raw is not None and math.isinf(raw),
        "likely_found_in_one_group_only": log2fc is not None
        and abs(log2fc) > OFF_SCALE_LOG2FC,
        "fdr": row["fdr"],
        "pct_1": _number(row.get("pct_1")),
        "pct_2": _number(row.get("pct_2")),
        "source": source,
    }
