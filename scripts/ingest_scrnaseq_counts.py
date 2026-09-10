#!/usr/bin/env python3
"""Write a single-cell dataset's per-gene expression into the explorer.

The cells have to be loaded first, by scripts/ingest_scrnaseq.py. This writes one
object per gene holding that gene's value for every cell, in cell order, plus a
row per gene so the search can find it.

Every value is served to the browser as a bare array with no cell identifiers in
it, paired against the cells purely by position. So this refuses to run unless
the dataset already holds these cells in this order -- compared barcode by
barcode against what the cell loader stored. That is the property the pairing
depends on, and checking it is the reason cells and counts are two steps rather
than one.

Run deliberately against a chosen database and storage:

    DATABASE_URL=postgresql://user:pass@host:5432/postgres \
    SUPABASE_URL=http://localhost:8000 \
    SUPABASE_SERVICE_KEY=... \
      uv run --with anndata --with 'psycopg[binary]' --with supabase \
        python scripts/ingest_scrnaseq_counts.py \
        --h5ad myb41_joint_SATURN_LABELS.h5ad \
        --dataset-name "MYB41 transgene" \
        --species-id 1 \
        --expect-nonzero AT4G28110.Fusion=232

Re-running is safe and resumes: a gene already recorded is skipped, so an
interrupted run costs only the genes it had not reached.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

BUCKET = "scrna"

# The explorer builds this path itself from the dataset and gene names, so it is
# a contract, not a choice: scrna-client.ts fetches counts/{dataset}/{gene}.bin.
COUNTS_PATH = "counts/{dataset}/{gene}.bin"

# Both names become part of an object path. A gene name is an accession, so it
# is held to accession characters. A dataset name is written by a person and the
# first one has a space in it, so it allows those -- but not the characters that
# would end the path early or move it somewhere else.
SAFE_GENE_NAME = re.compile(r"[A-Za-z0-9._-]+")
SAFE_DATASET_NAME = re.compile(r"[A-Za-z0-9._ -]+")

# A segment that is only dots addresses a directory rather than a file.
DOTS_ONLY = re.compile(r"\.+")

# How many genes to record in one round trip. Purely a speed knob: the objects
# are written one at a time either way, and the row always follows its object.
RECORD_BATCH = 500


class IngestError(RuntimeError):
    """Something about the file, the dataset or the storage makes this unsafe."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--h5ad", type=Path, required=True)
    p.add_argument("--dataset-name", required=True)
    p.add_argument("--species-id", type=int, required=True)
    p.add_argument(
        "--expect-nonzero",
        action="append",
        default=[],
        metavar="GENE=COUNT",
        help="refuse unless this gene is non-zero in exactly this many cells. "
             "Use it to pin a gene whose count is known independently, such as "
             "a transgene; repeatable",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="read and check everything, write nothing")
    return p.parse_args(argv)
def parse_expectations(pairs: list[str]) -> dict[str, int]:
    """`GENE=COUNT` arguments, refused rather than ignored when malformed."""
    out: dict[str, int] = {}
    for pair in pairs:
        name, sep, count = pair.partition("=")
        if not sep or not name or not count.isdigit():
            raise IngestError(
                f"--expect-nonzero wants GENE=COUNT, got {pair!r}"
            )
        out[name] = int(count)
    return out


def read_genes(h5ad_path: Path, expectations: dict[str, int]) -> dict:
    """Open the file and check everything about its genes before anything is
    written."""
    import anndata
    import numpy as np

    if not h5ad_path.exists():
        raise IngestError(f"no such file: {h5ad_path}")

    adata = anndata.read_h5ad(h5ad_path)
    if adata.n_obs == 0:
        raise IngestError(f"{h5ad_path.name} holds no cells")
    if adata.n_vars == 0:
        raise IngestError(f"{h5ad_path.name} holds no genes")

    names = [str(v) for v in adata.var_names]

    blank = sum(1 for g in names if not g.strip())
    if blank:
        raise IngestError(f"{blank} gene names are blank")

    unsafe = sorted({g for g in names
                     if not SAFE_GENE_NAME.fullmatch(g) or DOTS_ONLY.fullmatch(g)})
    if unsafe:
        raise IngestError(
            f"{len(unsafe)} gene names cannot be part of an object path: "
            f"{', '.join(unsafe[:5])}"
        )

    # Two genes with one name would write to one object, and the second would
    # silently replace the first for both of them.
    duplicates = sorted({g for g, n in _counts(names).items() if n > 1})
    if duplicates:
        raise IngestError(
            f"{len(duplicates)} gene names appear more than once, and would "
            f"share one object: {', '.join(duplicates[:5])}"
        )

    # Re-sliced by column once, so taking one gene's vector is cheap. The
    # matrix arrives row-major, where every gene read would touch every row.
    matrix = adata.X
    by_gene = matrix.tocsc() if hasattr(matrix, "tocsc") else np.asarray(matrix)

    for gene, expected in expectations.items():
        if gene not in names:
            raise IngestError(
                f"--expect-nonzero names {gene!r}, which is not in this file"
            )
        actual = int(np.count_nonzero(gene_vector(by_gene, names.index(gene))))
        if actual != expected:
            raise IngestError(
                f"{gene} is non-zero in {actual} cells, expected {expected}"
            )

    return {
        "n_cells": int(adata.n_obs),
        "barcodes": [str(v).strip() for v in adata.obs_names],
        "names": names,
        "by_gene": by_gene,
        "expectations": expectations,
    }


def _counts(names: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in names:
        out[name] = out.get(name, 0) + 1
    return out


def gene_vector(by_gene, column: int):
    """One gene's value for every cell, in cell order, as float32.

    float32 because that is what the browser reads it back as; anything wider
    would be silently truncated on the way in.
    """
    import numpy as np

    taken = by_gene[:, column]
    dense = (taken.toarray() if hasattr(taken, "toarray") else np.asarray(taken))
    dense = dense.ravel()
    return np.ascontiguousarray(dense, dtype="<f4")


def open_dataset(conn, name: str, species_id: int, barcodes: list[str]) -> int:
    """Find the loaded dataset, and refuse unless it holds these cells in this
    order.

    Everything written here is indexed by position against those cells, so a
    dataset whose cells are a different set -- or the same set in a different
    order -- would pair every gene with the wrong cell and look entirely normal
    doing it.

    The comparison is against the barcodes already stored, which is the property
    that actually has to hold. Hashing the file instead would refuse a
    re-export that carries the same cells in the same order, and would have
    nothing to say about a dataset loaded before the hash was recorded.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, n_cells FROM public.scrna_datasets "
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

        dataset_id, recorded_cells = found[0]
        cur.execute(
            "SELECT barcode FROM public.scrna_cells WHERE dataset_id = %s "
            "ORDER BY cell_number",
            (dataset_id,),
        )
        stored = [row[0] for row in cur.fetchall()]

    if len(stored) != len(barcodes):
        raise IngestError(
            f"dataset {dataset_id} holds {len(stored)} cells and this file has "
            f"{len(barcodes)}; the counts are paired to the cells by position"
        )
    for position, (was, now) in enumerate(zip(stored, barcodes)):
        if was != now:
            raise IngestError(
                f"dataset {dataset_id} does not hold these cells in this order: "
                f"cell {position} is {was!r} in the database and {now!r} in this "
                f"file. Reload the cells from this file first"
            )
    if recorded_cells is not None and recorded_cells != len(barcodes):
        raise IngestError(
            f"dataset {dataset_id} records {recorded_cells} cells but holds "
            f"{len(stored)}"
        )
    return dataset_id


def already_written(conn, dataset_id: int) -> set[str]:
    """Genes whose object is already recorded, so a re-run can skip them.

    The object is written before its row, so a run interrupted between the two
    leaves an object nothing points at -- which the next run simply overwrites.
    The reverse order would leave a row pointing at nothing, which the browser
    would hit as a broken gene.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT g.gene_name FROM public.scrna_counts c "
            "JOIN public.scrna_genes g ON g.id = c.gene_id "
            "WHERE c.dataset_id = %s",
            (dataset_id,),
        )
        return {row[0] for row in cur.fetchall()}


def register_genes(conn, dataset_id: int, names: list[str]) -> dict[str, int]:
    """Give every gene a row and a number, and hand back their ids.

    `gene_number` is the gene's position in the file, which is what makes a
    re-run land on the same numbers.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT gene_name, id FROM public.scrna_genes WHERE dataset_id = %s",
            (dataset_id,),
        )
        known = dict(cur.fetchall())

        missing = [(i, g) for i, g in enumerate(names) if g not in known]
        if missing:
            cur.executemany(
                "INSERT INTO public.scrna_genes "
                "(dataset_id, gene_number, gene_name) VALUES (%s, %s, %s)",
                [(dataset_id, number, gene) for number, gene in missing],
            )
            cur.execute(
                "SELECT gene_name, id FROM public.scrna_genes "
                "WHERE dataset_id = %s", (dataset_id,),
            )
            known = dict(cur.fetchall())

    if set(known) != set(names):
        raise IngestError(
            f"dataset {dataset_id} has {len(known)} genes registered but the "
            f"file holds {len(names)}; they do not describe the same dataset"
        )
    return known


def check_dataset_name(name: str) -> None:
    """The dataset name is a path segment too, and nothing else checks it.

    A name carrying a slash would write the whole dataset under a different
    prefix -- possibly over another dataset's genes -- and one carrying a query
    or fragment character would cut the path short when the browser asks for it
    back.
    """
    if not name.strip():
        raise IngestError("the dataset name is blank")
    if not SAFE_DATASET_NAME.fullmatch(name) or DOTS_ONLY.fullmatch(name):
        raise IngestError(
            f"dataset name {name!r} cannot be part of an object path; it may "
            f"hold letters, digits, spaces, and . _ -"
        )


def object_path(dataset_name: str, gene: str) -> str:
    return COUNTS_PATH.format(dataset=dataset_name, gene=gene)


def upload(storage, path: str, payload: bytes) -> None:
    """Put one gene's vector in the bucket, replacing whatever was there.

    Replacing rather than skipping is what makes a re-run repair a half-written
    object from an interrupted one.
    """
    storage.upload(
        path=path,
        file=payload,
        file_options={"content-type": "application/octet-stream",
                      "upsert": "true"},
    )


def write_counts(conn, storage, dataset_id: int, dataset_name: str,
                 genes: dict, gene_ids: dict[str, int],
                 skip: set[str]) -> tuple[int, int]:
    """Write every gene not already recorded. Returns written and skipped."""
    names = genes["names"]
    pending: list[tuple[int, int, str]] = []
    written = 0

    for number, gene in enumerate(names):
        if gene in skip:
            continue
        path = object_path(dataset_name, gene)
        upload(storage, path, gene_vector(genes["by_gene"], number).tobytes())
        pending.append((dataset_id, gene_ids[gene], path))
        written += 1
        if len(pending) >= RECORD_BATCH:
            _record(conn, pending)
            pending.clear()

    if pending:
        _record(conn, pending)
    return written, len(skip)


def _record(conn, rows: list[tuple[int, int, str]]) -> None:
    """Commit as it goes, deliberately.

    The cell loader is one transaction because a half-loaded dataset is worse
    than none. Here the opposite holds: each gene stands alone, and 27,656 of
    them is long enough that an interrupted run must keep the ground it made
    rather than start over.
    """
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO public.scrna_counts "
            "(dataset_id, gene_id, counts_object_path) VALUES (%s, %s, %s)",
            rows,
        )
    conn.commit()


def summarise(genes: dict, dataset_id: int, written: int, skipped: int) -> str:
    checked = ", ".join(f"{g} in {n} cells" for g, n in
                        sorted(genes["expectations"].items()))
    return (
        f"dataset {dataset_id}: {len(genes['names'])} genes over "
        f"{genes['n_cells']} cells\n  "
        f"{written} written, {skipped} already recorded"
        + (f"\n  checked: {checked}" if checked else "")
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        check_dataset_name(args.dataset_name)
        expectations = parse_expectations(args.expect_nonzero)
        genes = read_genes(args.h5ad, expectations)
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"{args.h5ad.name}: {len(genes['names'])} genes over "
              f"{genes['n_cells']} cells, every name usable as an object path")
        # Say which expectations were met, so a flag that was mistyped into
        # doing nothing is visible rather than reading as a pass.
        for gene, count in sorted(expectations.items()):
            print(f"  {gene} is non-zero in {count} cells, as expected")
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
            dataset_id = open_dataset(
                conn, args.dataset_name, args.species_id, genes["barcodes"],
            )
            gene_ids = register_genes(conn, dataset_id, genes["names"])
            conn.commit()
            skip = already_written(conn, dataset_id)
            written, skipped = write_counts(
                conn, storage, dataset_id, args.dataset_name, genes,
                gene_ids, skip,
            )
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"the database refused the load: {exc}", file=sys.stderr)
        return 1

    print(summarise(genes, dataset_id, written, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
