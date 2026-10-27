"""A dataset's default differential expression: each cluster against the rest, as scanpy's
`rank_genes_groups` stores it in the file's uns, loaded with the dataset.

It becomes one DE run (`scrna_de_runs`), one comparison per cluster (`scrna_de`: the cluster
against `rest`), every tested gene (`scrna_de_genes`), and each cluster's size, centre and top
markers (`scrna_cluster_stats`), in the shapes the expression page reads. A load that stops
continues the same run, found by its params_hash, and writes only what is missing.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._genes import strip_release
from ._text import visible
from ._writer import LoadError, Writer, insert, read_all

REST = "rest"
# Genes per insert request, like the cells.
GENE_BATCH = 5000
# What the cluster panel lists, and the cut it reads significant genes with.
TOP_MARKERS = 25
SIGNIFICANT_FDR = 0.05
# The parts of a rank_genes_groups result the load reads; pts and pts_rest are optional.
NEEDED = ("params", "names", "logfoldchanges", "pvals", "pvals_adj")
# The var column holding each gene's symbol, shown next to its id in the markers.
SYMBOL_COLUMN = "gene_symbols"


@dataclass(frozen=True)
class Comparison:
    """One cluster against the rest: its size, the rest's, and every tested gene's values."""

    cluster: str
    n_group1: int
    n_group2: int
    genes: list[dict]


@dataclass(frozen=True)
class Result:
    """The DE to load: how it was made, its fingerprint (the settings it was made with, which
    a resumed load of the same file shares), one comparison per cluster, and gene symbols."""

    method: str
    params: dict
    params_hash: str
    comparisons: list[Comparison]
    symbols: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def n_genes(self) -> int:
        return sum(len(c.genes) for c in self.comparisons)


def read(path: Path, key: str, annotation: str, cells: dict) -> Result:
    """The one-vs-rest result in uns[key], refused unless it compares the annotation's clusters
    with the rest."""
    from ._cells import _read_elem, _read_failures

    try:
        import h5py

        with h5py.File(path, "r") as f:
            if "uns" not in f or key not in f["uns"]:
                raise LoadError(f"{path.name} has no uns[{key!r}] to load differential "
                                "expression from")
            stored = _read_elem()(f["uns"][key])
            var = _read_elem()(f["var"])
    except _read_failures() as exc:
        raise LoadError(f"uns[{key!r}] in {path.name} could not be read: "
                        f"{visible(str(exc) or type(exc).__name__)}") from None
    symbols = (
        {strip_release(str(g)): str(s) for g, s in zip(var.index, var[SYMBOL_COLUMN]) if str(s)}
        if SYMBOL_COLUMN in getattr(var, "columns", ()) else {}
    )
    return _result(stored, key, annotation, cells, symbols)


def _result(stored: Any, key: str, annotation: str, cells: dict,
            symbols: dict[str, str] | None = None) -> Result:
    missing = [part for part in NEEDED if not isinstance(stored, dict) or part not in stored]
    if missing:
        raise LoadError(f"uns[{key!r}] isn't a rank_genes_groups result: it has no "
                        f"{', '.join(missing)}")
    params = {k: _plain(v) for k, v in dict(stored["params"]).items()}
    if params.get("reference") != REST:
        raise LoadError(f"uns[{key!r}] compares clusters with {params.get('reference')!r}, not "
                        "each with the rest; only a one-vs-rest result is loaded")
    if params.get("groupby") != annotation:
        raise LoadError(f"uns[{key!r}] groups cells by {params.get('groupby')!r}, but the "
                        f"dataset's clusters are {annotation!r}")
    sizes = Counter(cells["labels"])
    groups = list(stored["names"].dtype.names)
    unknown = [g for g in groups if g not in sizes]
    if unknown:
        raise LoadError(f"uns[{key!r}] has results for {', '.join(map(repr, unknown))}, which "
                        f"aren't clusters in obs[{annotation!r}]")
    total = sum(sizes.values())
    comparisons = [
        Comparison(g, sizes[g], total - sizes[g], _genes(stored, g)) for g in sorted(groups)
    ]
    method = f"scanpy-{params.get('method', 'unknown')}-one-vs-rest"
    fingerprint = json.dumps({"method": method, "params": params}, sort_keys=True)
    return Result(method, params, hashlib.sha256(fingerprint.encode()).hexdigest(), comparisons,
                  symbols or {})


def _genes(stored: dict, group: str) -> list[dict]:
    """Every gene tested for `group`, as scrna_de_genes stores it; a gene without a p-value
    wasn't tested and is left out."""
    names = stored["names"][group]
    pts, rest = stored.get("pts"), stored.get("pts_rest")
    rows = []
    for i, raw in enumerate(names):
        gene = strip_release(str(raw))
        pvalue = _probability(stored["pvals"][group][i])
        if pvalue is None:
            continue
        fdr = _probability(stored["pvals_adj"][group][i])
        rows.append({
            "gene": gene,
            "log2fc": _finite(stored["logfoldchanges"][group][i]),
            "pvalue": pvalue,
            "fdr": max(pvalue, fdr if fdr is not None else 1.0),
            "pct_1": _share(pts, raw, group),
            "pct_2": _share(rest, raw, group),
        })
    return rows


def _plain(value: Any) -> Any:
    """A params value as JSON: numpy scalars and arrays become Python ones."""
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, bytes):
        return value.decode()
    return value


def _finite(value: Any) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None


def _probability(value: Any) -> float | None:
    number = float(value)
    return None if math.isnan(number) else min(max(number, 0.0), 1.0)


def _share(frame: Any, gene: Any, group: str) -> float | None:
    """The share of cells expressing `gene`, from scanpy's pts table, or None without one."""
    if frame is None:
        return None
    try:
        return _probability(frame.loc[gene, group])
    except KeyError:
        return None


def markers(comparison: Comparison, symbols: dict[str, str]) -> dict:
    """The cluster panel's markers: the top up-regulated significant genes, and how many."""
    significant = [g for g in comparison.genes
                   if g["fdr"] < SIGNIFICANT_FDR and (g["log2fc"] or 0) > 0]
    significant.sort(key=lambda g: (g["fdr"], -(g["log2fc"] or 0)))
    top = []
    for g in significant[:TOP_MARKERS]:
        entry = {"gene": g["gene"], "log2fc": g["log2fc"], "q": g["fdr"],
                 "pct_1": g["pct_1"], "pct_2": g["pct_2"]}
        if symbols.get(g["gene"]):
            entry["symbol"] = symbols[g["gene"]]
        top.append(entry)
    return {"top": top, "n_significant": len(significant)}


def write(writer: Writer, dataset_id: int, result: Result, cells: dict, report=None) -> None:
    """Write what the dataset is missing of `result`, continuing its run if one was started."""
    run_id = _run(writer, dataset_id, result)
    gene_ids = {r["gene_name"]: r["id"] for r in read_all(
        writer, "scrna_genes", "id, gene_name", filters=[("eq", "dataset_id", dataset_id)])}
    unknown = sorted({g["gene"] for c in result.comparisons for g in c.genes} - set(gene_ids))
    if unknown:
        raise LoadError(f"the differential expression names {len(unknown)} gene(s) the dataset "
                        f"doesn't have, e.g. {visible(unknown[0])!r}")
    written = {r["cluster_id"]: r["id"] for r in read_all(
        writer, "scrna_de", "id, cluster_id", filters=[("eq", "run_id", run_id)])}
    done = 0
    for comparison in result.comparisons:
        de_id = written.get(comparison.cluster) or _comparison(writer, dataset_id, run_id,
                                                               result, comparison)
        done += _write_genes(writer, dataset_id, de_id, comparison, gene_ids)
        if report:
            report(done, result.n_genes)
    _write_stats(writer, dataset_id, result, cells)


def _run(writer: Writer, dataset_id: int, result: Result) -> int:
    found = read_all(writer, "scrna_de_runs", "id", filters=[
        ("eq", "dataset_id", dataset_id), ("eq", "params_hash", result.params_hash)])
    if found:
        return found[0]["id"]
    (row,) = insert(writer, "record the differential expression", "scrna_de_runs", [{
        "dataset_id": dataset_id, "source": "batch", "status": "complete",
        "method": result.method, "params": result.params, "params_hash": result.params_hash,
        "completed_at": datetime.now(UTC).isoformat()}], returning=True)
    return row["id"]


def _comparison(writer: Writer, dataset_id: int, run_id: int, result: Result,
                comparison: Comparison) -> int:
    tested = bool(comparison.genes)
    (row,) = insert(writer, f"record cluster {comparison.cluster} against the rest", "scrna_de", [{
        "dataset_id": dataset_id, "run_id": run_id, "cluster_id": comparison.cluster,
        "contrast": f"{comparison.cluster}_vs_{REST}", "group1": comparison.cluster,
        "group2": REST, "n_group1": comparison.n_group1, "n_group2": comparison.n_group2,
        "group_kind": "cluster", "method": result.method, "params_hash": result.params_hash,
        "tested": tested, "n_genes_tested": len(comparison.genes)}], returning=True)
    return row["id"]


def _write_genes(writer: Writer, dataset_id: int, de_id: int, comparison: Comparison,
                 gene_ids: dict[str, int]) -> int:
    """Insert the comparison's genes not stored yet; how many genes it holds in all."""
    have = {r["gene_id"] for r in read_all(
        writer, "scrna_de_genes", "gene_id", filters=[("eq", "de_id", de_id)], order="gene_id")}
    rows = [{"de_id": de_id, "dataset_id": dataset_id, "gene_id": gene_ids[g["gene"]],
             **{k: g[k] for k in ("log2fc", "pvalue", "fdr", "pct_1", "pct_2")}}
            for g in comparison.genes if gene_ids[g["gene"]] not in have]
    for start in range(0, len(rows), GENE_BATCH):
        insert(writer, f"store cluster {comparison.cluster}'s genes", "scrna_de_genes",
               rows[start:start + GENE_BATCH])
    return len(comparison.genes)


def _write_stats(writer: Writer, dataset_id: int, result: Result, cells: dict) -> None:
    have = {r["cluster_id"] for r in read_all(
        writer, "scrna_cluster_stats", "cluster_id", filters=[("eq", "dataset_id", dataset_id)],
        order="cluster_id")}
    total = cells["n_cells"]
    rows = []
    for comparison in result.comparisons:
        if comparison.cluster in have:
            continue
        xs, ys = _positions(cells, comparison.cluster)
        rows.append({
            "dataset_id": dataset_id, "cluster_id": comparison.cluster,
            "cell_count": comparison.n_group1, "pct": 100.0 * comparison.n_group1 / total,
            "centroid_x": sum(xs) / len(xs) if xs else None,
            "centroid_y": sum(ys) / len(ys) if ys else None,
            "markers": markers(comparison, result.symbols),
        })
    if rows:
        insert(writer, "store each cluster's statistics", "scrna_cluster_stats", rows)


def _positions(cells: dict, cluster: str) -> tuple[list[float], list[float]]:
    picked = [i for i, label in enumerate(cells["labels"]) if label == cluster]
    return [cells["x"][i] for i in picked], [cells["y"][i] for i in picked]


def describe(result: Result) -> str:
    """One line for the summary shown before the question."""
    n = len(result.comparisons)
    return (f"{n} cluster{'' if n == 1 else 's'} each against the rest ({result.method}), "
            f"{result.n_genes:,} gene results")
