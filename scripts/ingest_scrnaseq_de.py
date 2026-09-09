#!/usr/bin/env python3
"""Write a single-cell dataset's differential expression into the explorer.

Reads the two files the analysis produces -- the summary of every comparison
that was considered, and the full per-gene results for the ones that ran -- and
writes one object per comparison that ran, plus a row per comparison either way.

A comparison that was skipped still gets a row. The panel needs to be able to
say "this was not tested" rather than leaving a cell type silently absent, and
the group sizes explain why it was skipped.

The summary is not trusted. Every count in it is recomputed from the results
file and the row is refused if they disagree, because the summary is what the
panel shows and the results file is what a reader would check it against.

Run deliberately against a chosen database and storage:

    DATABASE_URL=postgresql://user:pass@host:5432/postgres \
    SUPABASE_URL=http://localhost:8000 \
    SUPABASE_SERVICE_KEY=... \
      uv run --with 'psycopg[binary]' --with supabase \
        python scripts/ingest_scrnaseq_de.py \
        --results ALL_LEVEL1_DE_RESULTS.tsv \
        --summary LEVEL1_DE_SUMMARY.tsv \
        --dataset-name "MYB41 transgene" \
        --species-id 1

Re-running replaces this dataset's differential expression entirely.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

BUCKET = "scrna"

# The panel downloads whatever path the row carries, so this is ours to choose.
# It has to survive being a path segment and stay one-to-one with the cell type.
DE_PATH = "de/{dataset}/{celltype}__{contrast}.json"
UNSAFE_IN_PATH = re.compile(r"[^A-Za-z0-9._-]+")

# The results name genes with the annotation release appended; the expression
# matrix does not. Stripping it is what lets a gene in the table be looked up in
# the rest of the explorer. Checked against the registered genes, not assumed.
GENE_SUFFIX = re.compile(r"\.Araport11\.\d+$")

# The column names the panel reads, which are Seurat's rather than this
# analysis's. web/components/expression-differential-analysis.tsx.
RESULT_COLUMNS = ("gene", "p_val", "avg_log2FC", "pct.1", "pct.2",
                  "p_val_adj", "_row")

# Fields the two files must carry. Named so a changed export is refused with
# something an operator can act on rather than a KeyError.
SUMMARY_FIELDS = ("celltype", "contrast", "group1", "group2", "n_group1",
                  "n_group2", "tested", "n_genes_tested", "n_FDR_0.05",
                  "n_FDR_0.05_abs_log2FC_0.5", "n_up", "n_down")
RESULT_FIELDS = ("celltype", "contrast", "gene", "log2FC", "pvalue", "FDR",
                 "pct_expr_group1", "pct_expr_group2", "sig_FDR_0.05",
                 "sig_FDR_0.05_abs_log2FC_0.5")


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
    """The exports write Python booleans. Anything else is refused rather than
    guessed at, because guessing wrong silently changes every count."""
    if value not in ("True", "False"):
        raise IngestError(f"expected True or False, got {value!r}")
    return value == "True"


def safe_segment(name: str) -> str:
    return UNSAFE_IN_PATH.sub("_", name)


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
    """The per-gene rows, grouped by the comparison they belong to, already in
    the shape the panel reads."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    suffixed = 0
    for row in _rows(path, RESULT_FIELDS):
        gene = GENE_SUFFIX.sub("", row["gene"])
        if gene != row["gene"]:
            suffixed += 1
        grouped[(row["celltype"], row["contrast"])].append({
            "gene": gene,
            "p_val": float(row["pvalue"]),
            "avg_log2FC": float(row["log2FC"]),
            "pct.1": float(row["pct_expr_group1"]),
            "pct.2": float(row["pct_expr_group2"]),
            "p_val_adj": float(row["FDR"]),
            "_row": gene,
            # Kept out of the written row; the counts are recomputed from them.
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
    return {
        "n_genes_tested": len(rows),
        "n_significant_fdr": sum(1 for r in rows if r["_fdr"]),
        "n_significant_fdr_lfc": len(significant),
        "n_up": sum(1 for r in significant if r["avg_log2FC"] > 0),
        "n_down": sum(1 for r in significant if r["avg_log2FC"] <= 0),
    }


def reconcile(summary: list[dict], groups: dict[tuple[str, str], list[dict]]) -> None:
    """Refuse any summary row the results file does not bear out.

    The summary is what the panel puts on screen and the results file is what a
    reader would check it against, so a disagreement is not something to record
    and move past.
    """
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


def check_paths(summary: list[dict], dataset_name: str) -> dict[tuple[str, str], str]:
    """One object path per tested comparison, and no two the same.

    Two cell types whose names differ only in punctuation would otherwise share
    a path, and the second written would replace the first for both.
    """
    paths: dict[tuple[str, str], str] = {}
    for entry in summary:
        if not entry["tested"]:
            continue
        key = (entry["celltype"], entry["contrast"])
        paths[key] = DE_PATH.format(
            dataset=safe_segment(dataset_name),
            celltype=safe_segment(entry["celltype"]),
            contrast=safe_segment(entry["contrast"]),
        )
    collisions = defaultdict(list)
    for key, path in paths.items():
        collisions[path].append(key)
    clashing = {p: k for p, k in collisions.items() if len(k) > 1}
    if clashing:
        path, keys = next(iter(clashing.items()))
        raise IngestError(
            f"{len(clashing)} comparisons would share one object: "
            f"{' and '.join(c for c, _ in keys)} both become {path}"
        )
    return paths


def check_cell_types(conn, dataset_id: int, summary: list[dict]) -> None:
    """Every cell type named here has to exist in the catalogue.

    The panel lists cell types straight from these rows, so one the map has
    never heard of is offered and then colours nothing.
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
            f"{len(unknown)} cell types are not in the catalogue, so the panel "
            f"would offer cell types the map does not have: "
            f"{', '.join(unknown[:5])}"
        )


def check_genes(conn, dataset_id: int,
                groups: dict[tuple[str, str], list[dict]]) -> tuple[int, int]:
    """Cross-check gene names against the ones registered for this dataset.

    Returns how many were checked and how many are registered. Zero registered
    means the gene counts have not been loaded, which is not an error here --
    but it is reported, so an unchecked run is visible rather than silent.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT gene_name FROM public.scrna_genes WHERE dataset_id = %s",
            (dataset_id,),
        )
        registered = {row[0] for row in cur.fetchall()}
    if not registered:
        return 0, 0

    named = {r["gene"] for rows in groups.values() for r in rows}
    missing = sorted(named - registered)
    if missing:
        raise IngestError(
            f"{len(missing)} genes in the results are not registered for this "
            f"dataset, so nothing in the table could be looked up: "
            f"{', '.join(missing[:5])}"
        )
    return len(named), len(registered)


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


def as_json(rows: list[dict]) -> bytes:
    """Only the columns the panel reads, in the order it declares them."""
    return json.dumps(
        [{c: r[c] for c in RESULT_COLUMNS} for r in rows]
    ).encode("utf-8")


def write_de(conn, storage, dataset_id: int, summary: list[dict],
             groups: dict[tuple[str, str], list[dict]],
             paths: dict[tuple[str, str], str]) -> tuple[int, int]:
    """Replace this dataset's differential expression, in one transaction.

    Unlike the gene counts there are 69 rows and 46 objects, so this is small
    enough to be all-or-nothing -- and it should be, because the rows and the
    objects only make sense together.
    """
    for key, path in paths.items():
        storage.upload(
            path=path,
            file=as_json(groups[key]),
            file_options={"content-type": "application/json",
                          "upsert": "true"},
        )

    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.scrna_de WHERE dataset_id = %s",
                    (dataset_id,))
        cur.executemany(
            "INSERT INTO public.scrna_de "
            "(dataset_id, cluster_id, contrast, group1, group2, n_group1, "
            " n_group2, file_path, n_genes_tested, n_significant_fdr, "
            " n_significant_fdr_lfc, n_up, n_down) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    dataset_id, e["celltype"], e["contrast"], e["group1"],
                    e["group2"], e["n_group1"], e["n_group2"],
                    paths.get((e["celltype"], e["contrast"])),
                    # A skipped comparison stores five zeros rather than five
                    # blanks: it names a contrast, and a row that names one
                    # carries all five counts. Blanks would also switch off the
                    # arithmetic rules that compare them.
                    *( (e["counts"]["n_genes_tested"],
                        e["counts"]["n_significant_fdr"],
                        e["counts"]["n_significant_fdr_lfc"],
                        e["counts"]["n_up"], e["counts"]["n_down"])
                       if e["tested"] else (0, 0, 0, 0, 0) ),
                )
                for e in summary
            ],
        )
    return len(paths), len(summary)


def summarise(summary: list[dict], read: dict, checked: tuple[int, int]) -> str:
    tested = sum(1 for e in summary if e["tested"])
    named, registered = checked
    genes = (f"{named} genes, all registered for this dataset"
             if registered else
             "gene names not cross-checked: no genes are registered for this "
             "dataset yet")
    return (
        f"{len(summary)} comparisons: {tested} tested, {len(summary) - tested} "
        f"skipped\n  every count in the summary agrees with the results\n  "
        f"{genes}\n  "
        f"annotation release stripped from {read['suffixed']} gene names"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        summary = read_summary(args.summary)
        read = read_results(args.results)
        reconcile(summary, read["groups"])
        paths = check_paths(summary, args.dataset_name)
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        tested = sum(1 for e in summary if e["tested"])
        print(f"{args.summary.name}: {len(summary)} comparisons, {tested} "
              f"tested, {len(summary) - tested} skipped")
        print("  every count in the summary agrees with the results")
        print(f"  annotation release stripped from {read['suffixed']} gene names")
        print(f"  {len(paths)} objects would be written, one per tested "
              f"comparison")
        print("dry run — nothing written")
        return 0

    database_url = os.getenv("DATABASE_URL")
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_KEY")
    if not (database_url and supabase_url and service_key):
        print(
            "DATABASE_URL, SUPABASE_URL and SUPABASE_SERVICE_KEY are all "
            "required: the rows go straight to the database, and the objects "
            "go through the storage API so the browser can read them back "
            "under the same policies as every other private bucket.",
            file=sys.stderr,
        )
        return 1

    import psycopg
    from supabase import create_client

    storage = create_client(supabase_url, service_key).storage.from_(BUCKET)

    try:
        with psycopg.connect(database_url) as conn:
            dataset_id = open_dataset(conn, args.dataset_name, args.species_id)
            check_cell_types(conn, dataset_id, summary)
            checked = check_genes(conn, dataset_id, read["groups"])
            objects, rows = write_de(conn, storage, dataset_id, summary,
                                     read["groups"], paths)
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"the database refused the load: {exc}", file=sys.stderr)
        return 1

    print(summarise(summary, read, checked))
    print(f"  wrote {objects} objects and {rows} rows for dataset {dataset_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
