#!/usr/bin/env python3
"""Write a single-cell dataset's differential expression into the explorer.

Reads the two files the analysis produces -- the summary of every comparison it
considered, and the per-gene results for the ones that ran -- and records them as
one analysis: a run, a row per comparison, and a row per gene tested.

A skipped comparison still gets a row, marked untested, so the panel can say
"not tested" rather than leaving a cell type silently absent.

The summary's counts are not stored, but they are checked: each is recomputed
from the results, and the load is refused if they disagree.

Run deliberately against a chosen database:

    DATABASE_URL=postgresql://user:pass@host:5432/postgres \
      uv run --with 'psycopg[binary]' \
        python scripts/ingest_scrnaseq_de.py \
        --results ALL_LEVEL1_DE_RESULTS.tsv \
        --summary LEVEL1_DE_SUMMARY.tsv \
        --dataset-name "MYB41 transgene" \
        --species-id 1 \
        --method seurat-wilcoxon

Loading again adds a new analysis beside the old one; loading the same two files
twice is refused. Everything is written in one transaction.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# The results name genes with the annotation release appended; the expression
# matrix does not. Stripping it is what lets a gene be looked up in the catalogue.
GENE_SUFFIX = re.compile(r"\.Araport11\.\d+$")

# The export compares genotypes within a cell type.
GROUP_KIND = "genotype"

# Gene rows per INSERT. An analysis is roughly 675k rows.
GENE_BATCH = 10_000

# Fields the two files must carry, so a changed export is refused by name.
SUMMARY_FIELDS = ("celltype", "contrast", "group1", "group2", "n_group1",
                  "n_group2", "tested", "n_genes_tested", "n_FDR_0.05",
                  "n_FDR_0.05_abs_log2FC_0.5", "n_up", "n_down")
RESULT_FIELDS = ("celltype", "contrast", "gene", "log2FC", "pvalue", "FDR",
                 "pct_expr_group1", "pct_expr_group2", "sig_FDR_0.05",
                 "sig_FDR_0.05_abs_log2FC_0.5")

INSERT_RUN = (
    "INSERT INTO public.scrna_de_runs "
    "(dataset_id, source, status, method, params, params_hash, completed_at) "
    "VALUES (%s, 'batch', 'complete', %s, %s::jsonb, %s, now()) RETURNING id"
)
INSERT_RESULT = (
    "INSERT INTO public.scrna_de "
    "(dataset_id, run_id, cluster_id, contrast, group1, group2, n_group1, "
    " n_group2, group_kind, method, params_hash, tested) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"
)
INSERT_GENES = (
    "INSERT INTO public.scrna_de_genes "
    "(de_id, dataset_id, gene_id, log2fc, pvalue, fdr, pct_1, pct_2) "
    "SELECT * FROM unnest(%s::bigint[], %s::bigint[], %s::bigint[], "
    "%s::real[], %s::float8[], %s::float8[], %s::real[], %s::real[])"
)


class IngestError(RuntimeError):
    """Something about the files or the dataset makes this unsafe to write."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--results", type=Path, required=True,
                   help="ALL_LEVEL1_DE_RESULTS.tsv — every gene of every "
                        "comparison that ran")
    p.add_argument("--summary", type=Path, required=True,
                   help="LEVEL1_DE_SUMMARY.tsv — one row per comparison "
                        "considered, tested or not")
    p.add_argument("--dataset-name", required=True)
    p.add_argument("--species-id", type=int, required=True)
    p.add_argument("--method", required=True,
                   help="the test that produced the results, e.g. "
                        "seurat-wilcoxon; recorded on the analysis")
    p.add_argument("--dry-run", action="store_true",
                   help="read and check everything, write nothing")
    return p.parse_args(argv)


def _rows(path: Path, required: tuple[str, ...]) -> list[dict]:
    if not path.exists():
        raise IngestError(f"no such file: {path}")
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise IngestError(
                f"{path.name} has no column {', '.join(missing)}; found "
                f"{', '.join(reader.fieldnames or ['nothing'])}"
            )
        rows = list(reader)
    if not rows:
        raise IngestError(f"{path.name} holds no rows")
    return rows


def _flag(value: str) -> bool:
    """The exports write Python booleans; anything else is refused, not guessed."""
    if value not in ("True", "False"):
        raise IngestError(f"expected True or False, got {value!r}")
    return value == "True"


def _where(row: dict) -> str:
    """Enough of a results row to find it in a file of hundreds of thousands."""
    return f"{row['celltype']} / {row['contrast']}, gene {row['gene']}"


def _number(row: dict, column: str) -> float:
    """A finite number from one results column, or a refusal naming the gene."""
    value = row[column]
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise IngestError(
            f"{_where(row)}: {column} is {value!r}, which is not a number"
        ) from None
    if not math.isfinite(number):
        raise IngestError(f"{_where(row)}: {column} is {value!r}, not a finite number")
    return number


def _fraction(row: dict, column: str) -> float:
    """A value between 0 and 1: a probability or a proportion of cells."""
    number = _number(row, column)
    if not 0 <= number <= 1:
        hint = "; it looks like a percentage" if 1 < number <= 100 else ""
        raise IngestError(
            f"{_where(row)}: {column} is {number}, outside 0 to 1{hint}"
        )
    return number


def _fold_change(row: dict) -> float | None:
    """log2FC, or None where the analysis could not compute one.

    R writes NA or NaN when both groups express nothing. That is stored as no
    fold change. An infinite one is kept: it is a measurement, not a gap.
    """
    if row["log2FC"] == "NA":
        return None
    try:
        number = float(row["log2FC"])
    except (TypeError, ValueError):
        raise IngestError(
            f"{_where(row)}: log2FC is {row['log2FC']!r}, which is not a number"
        ) from None
    return None if math.isnan(number) else number


def read_summary(path: Path) -> list[dict]:
    """One row per comparison the analysis considered."""
    out = []
    for row in _rows(path, SUMMARY_FIELDS):
        tested = _flag(row["tested"])
        entry = {
            "celltype": row["celltype"],
            "contrast": row["contrast"],
            "group1": row["group1"],
            "group2": row["group2"],
            "n_group1": int(row["n_group1"]),
            "n_group2": int(row["n_group2"]),
            "tested": tested,
            "counts": {
                "n_genes_tested": int(row["n_genes_tested"]),
                "n_significant_fdr": int(row["n_FDR_0.05"]),
                "n_significant_fdr_lfc": int(row["n_FDR_0.05_abs_log2FC_0.5"]),
                "n_up": int(float(row["n_up"])),
                "n_down": int(float(row["n_down"])),
            } if tested else None,
        }
        if entry["group1"] == entry["group2"]:
            raise IngestError(
                f"{entry['celltype']} / {entry['contrast']} compares "
                f"{entry['group1']} against itself"
            )
        out.append(entry)

    seen = {(e["celltype"], e["contrast"]) for e in out}
    if len(seen) != len(out):
        raise IngestError(
            f"{path.name} names the same comparison twice; there must be one "
            f"row per cell type and contrast"
        )
    return out


def read_results(path: Path) -> dict:
    """The per-gene rows, grouped by the comparison they belong to."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    suffixed = 0
    for row in _rows(path, RESULT_FIELDS):
        gene = GENE_SUFFIX.sub("", row["gene"])
        if gene != row["gene"]:
            suffixed += 1
        pvalue, fdr = _fraction(row, "pvalue"), _fraction(row, "FDR")
        if fdr < pvalue:
            raise IngestError(
                f"{_where(row)}: FDR {fdr} is below its own p-value {pvalue}"
            )
        grouped[(row["celltype"], row["contrast"])].append({
            "gene": gene,
            "log2fc": _fold_change(row),
            "pvalue": pvalue,
            "fdr": fdr,
            "pct_1": _fraction(row, "pct_expr_group1"),
            "pct_2": _fraction(row, "pct_expr_group2"),
            # Not written; the summary's counts are checked against them.
            "_fdr": _flag(row["sig_FDR_0.05"]),
            "_fdr_lfc": _flag(row["sig_FDR_0.05_abs_log2FC_0.5"]),
        })

    for key, rows in grouped.items():
        names = [r["gene"] for r in rows]
        if len(set(names)) != len(names):
            raise IngestError(
                f"{key[0]} / {key[1]} names the same gene twice once the "
                f"annotation release is stripped from it"
            )
    return {"groups": dict(grouped), "suffixed": suffixed}


def recount(rows: list[dict]) -> dict[str, int]:
    """What the summary should say, worked out from the rows themselves."""
    significant = [r for r in rows if r["_fdr_lfc"]]
    up = sum(1 for r in significant if r["log2fc"] is not None and r["log2fc"] > 0)
    return {
        "n_genes_tested": len(rows),
        "n_significant_fdr": sum(1 for r in rows if r["_fdr"]),
        "n_significant_fdr_lfc": len(significant),
        "n_up": up,
        "n_down": len(significant) - up,
    }


def reconcile(summary: list[dict], groups: dict[tuple[str, str], list[dict]]) -> None:
    """Refuse any summary row the results file does not bear out."""
    for entry in summary:
        key = (entry["celltype"], entry["contrast"])
        rows = groups.get(key)
        if not entry["tested"]:
            if rows:
                raise IngestError(
                    f"{key[0]} / {key[1]} is marked untested but the results "
                    f"hold {len(rows)} genes for it"
                )
            continue
        if not rows:
            raise IngestError(
                f"{key[0]} / {key[1]} is marked tested but the results hold no "
                f"genes for it"
            )
        counted = recount(rows)
        for field, expected in entry["counts"].items():
            if counted[field] != expected:
                raise IngestError(
                    f"{key[0]} / {key[1]}: the summary says {field} is "
                    f"{expected}, the results give {counted[field]}"
                )

    extra = set(groups) - {(e["celltype"], e["contrast"]) for e in summary}
    if extra:
        first = sorted(extra)[:3]
        raise IngestError(
            f"{len(extra)} comparisons appear in the results with no summary "
            f"row: {', '.join(f'{c} / {k}' for c, k in first)}"
        )


def fingerprint(summary: Path, results: Path) -> tuple[dict, str]:
    """What this analysis was loaded from, and a hash that identifies it.

    Two loads with the same hash are the same files, so the second is refused
    rather than recorded as a second analysis that agrees with the first.
    """
    params = {
        "summary_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
        "results_sha256": hashlib.sha256(results.read_bytes()).hexdigest(),
    }
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return params, hashlib.sha256(canonical.encode()).hexdigest()


def open_dataset(conn, name: str, species_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM public.scrna_datasets "
            "WHERE name = %s AND species_id = %s AND deleted_at IS NULL",
            (name, species_id),
        )
        found = cur.fetchall()
    if not found:
        raise IngestError(
            f"no dataset named {name!r} for species {species_id}. Load its "
            f"cells first with scripts/ingest_scrnaseq.py"
        )
    if len(found) > 1:
        raise IngestError(
            f"{len(found)} datasets are named {name!r} for species "
            f"{species_id}; cannot tell which to write to"
        )
    return found[0][0]


def check_cell_types(conn, dataset_id: int, summary: list[dict]) -> None:
    """Every cell type named here has to exist in the dataset's catalogue.

    The database refuses it too; this says which ones and what to do about it.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cluster_id FROM public.scrna_clusters WHERE dataset_id = %s",
            (dataset_id,),
        )
        catalogue = {row[0] for row in cur.fetchall()}
    if not catalogue:
        raise IngestError(
            f"dataset {dataset_id} has no cell types. Load its cells first with "
            f"scripts/ingest_scrnaseq.py"
        )
    unknown = sorted({e["celltype"] for e in summary} - catalogue)
    if unknown:
        raise IngestError(
            f"{len(unknown)} cell types are not in the catalogue: "
            f"{', '.join(unknown[:5])}"
        )


def gene_ids(conn, dataset_id: int,
             groups: dict[tuple[str, str], list[dict]]) -> dict[str, int]:
    """The catalogue id of every gene the results name.

    Gene rows reference the catalogue, so the genes have to be registered first,
    and a name registered twice cannot be resolved to one gene.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT gene_name, id FROM public.scrna_genes WHERE dataset_id = %s",
            (dataset_id,),
        )
        registered = cur.fetchall()
    if not registered:
        raise IngestError(
            f"dataset {dataset_id} has no registered genes. Load its gene "
            f"counts first with scripts/ingest_scrnaseq_counts.py"
        )
    ids: dict[str, int] = {}
    twice = set()
    for name, gene_id in registered:
        if name in ids:
            twice.add(name)
        ids[name] = gene_id

    named = {r["gene"] for rows in groups.values() for r in rows}
    ambiguous = sorted(named & twice)
    if ambiguous:
        raise IngestError(
            f"{len(ambiguous)} genes are registered more than once for this "
            f"dataset, so a result cannot say which it means: "
            f"{', '.join(ambiguous[:5])}"
        )
    missing = sorted(named - set(ids))
    if missing:
        raise IngestError(
            f"{len(missing)} genes in the results are not registered for this "
            f"dataset: {', '.join(missing[:5])}"
        )
    return ids


def check_not_loaded(conn, dataset_id: int, params_hash: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM public.scrna_de_runs "
            "WHERE dataset_id = %s AND params_hash = %s",
            (dataset_id, params_hash),
        )
        found = cur.fetchone()
    if found:
        raise IngestError(
            f"these two files were already loaded for dataset {dataset_id} as "
            f"analysis {found[0]}"
        )


def write_de(conn, dataset_id: int, method: str, params: dict,
             params_hash: str, summary: list[dict],
             groups: dict[tuple[str, str], list[dict]],
             ids: dict[str, int]) -> tuple[int, int, int]:
    """Record the analysis, its comparisons and their genes.

    Runs inside the caller's transaction, so a refusal anywhere leaves nothing.
    Returns the run id, the comparisons written and the gene rows written.
    """
    genes = 0
    with conn.cursor() as cur:
        cur.execute(INSERT_RUN, (dataset_id, method, json.dumps(params),
                                 params_hash))
        run_id = cur.fetchone()[0]
        for e in summary:
            cur.execute(INSERT_RESULT, (
                dataset_id, run_id, e["celltype"], e["contrast"], e["group1"],
                e["group2"], e["n_group1"], e["n_group2"], GROUP_KIND, method,
                params_hash, e["tested"],
            ))
            de_id = cur.fetchone()[0]
            rows = groups.get((e["celltype"], e["contrast"]), [])
            for start in range(0, len(rows), GENE_BATCH):
                batch = rows[start:start + GENE_BATCH]
                cur.execute(INSERT_GENES, (
                    [de_id] * len(batch), [dataset_id] * len(batch),
                    [ids[r["gene"]] for r in batch],
                    [r["log2fc"] for r in batch], [r["pvalue"] for r in batch],
                    [r["fdr"] for r in batch], [r["pct_1"] for r in batch],
                    [r["pct_2"] for r in batch],
                ))
            genes += len(rows)
    return run_id, len(summary), genes


def describe(summary: list[dict], read: dict) -> str:
    tested = sum(1 for e in summary if e["tested"])
    genes = sum(len(rows) for rows in read["groups"].values())
    no_fold = sum(1 for rows in read["groups"].values() for r in rows
                  if r["log2fc"] is None)
    return (
        f"{len(summary)} comparisons: {tested} tested, {len(summary) - tested} "
        f"skipped\n"
        f"  every count in the summary agrees with the results\n"
        f"  {genes} gene results, {no_fold} with no fold change\n"
        f"  annotation release stripped from {read['suffixed']} gene names"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        summary = read_summary(args.summary)
        read = read_results(args.results)
        reconcile(summary, read["groups"])
        params, params_hash = fingerprint(args.summary, args.results)
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1

    print(describe(summary, read))
    if args.dry_run:
        print("dry run — nothing written")
        return 0

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required to write.", file=sys.stderr)
        return 1

    import psycopg

    try:
        with psycopg.connect(database_url) as conn:
            dataset_id = open_dataset(conn, args.dataset_name, args.species_id)
            check_cell_types(conn, dataset_id, summary)
            ids = gene_ids(conn, dataset_id, read["groups"])
            check_not_loaded(conn, dataset_id, params_hash)
            run_id, results, genes = write_de(
                conn, dataset_id, args.method, params, params_hash, summary,
                read["groups"], ids)
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"the database refused the load: {exc}", file=sys.stderr)
        return 1

    print(f"  wrote analysis {run_id}: {results} comparisons and {genes} gene "
          f"rows for dataset {dataset_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
