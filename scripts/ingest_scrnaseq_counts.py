#!/usr/bin/env python3
"""Write a single-cell dataset's per-gene expression into the explorer.

The cells have to be loaded first, by scripts/ingest_scrnaseq.py. This writes one
object per gene holding that gene's value for every cell, in cell order, plus a
row per gene so the search can find it.

Every value is served to the browser as a bare array with no cell identifiers in
it, paired against the cells purely by position. So this refuses to run unless
the dataset already holds these cells in this order -- compared barcode by
barcode against what the cell loader stored.

It signs in to the site as a writer (or admin) account and writes through the
API. The password is read from BLOOM_PASSWORD, never from the command line:

    BLOOM_PASSWORD=... uv run --with anndata --with supabase \
      python scripts/ingest_scrnaseq_counts.py \
        --server https://staging.bloom.salk.edu --email you@salk.edu \
        --h5ad myb41_joint_SATURN_LABELS.h5ad \
        --dataset-name "MYB41 transgene" --species-id 1 \
        --expect-nonzero AT4G28110.Fusion=232

--api-url and --anon-key give the API address instead of --server. How an admin
makes an account a writer is in scripts/ingest_scrnaseq.py.

If a load stops, run the same command again: a gene already recorded is skipped.
Load a dataset from one terminal at a time.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrna_ingest_api as ingest_api

IngestError = ingest_api.IngestError

BUCKET = "scrna"

# Recorded on scrna_counts.counts_object_path and read back from there, so the
# shape of this path is ours to choose -- scrna-client.ts follows the row rather
# than rebuilding the path. The object itself is the sparse JSON gene_counts()
# writes, which is what fetchGeneCounts parses.
COUNTS_PATH = "counts/{dataset}/{gene}.json"

# A gene name becomes part of an object path, so it is held to accession
# characters. The dataset name's rule is shared with the cells loader.
SAFE_GENE_NAME = re.compile(r"[A-Za-z0-9._-]+")
DOTS_ONLY = ingest_api.DOTS_ONLY

# Genes recorded per request. The objects are uploaded one at a time either way,
# and a row always follows its object.
RECORD_BATCH = 500

# Genes registered per request.
GENE_BATCH = 5000


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
    p.add_argument("--server", help="the site, e.g. https://staging.bloom.salk.edu; "
                   "the API address is read from it")
    p.add_argument("--api-url", help="the API address, instead of reading it from --server")
    p.add_argument("--anon-key", help="the site's public key, with --api-url")
    p.add_argument("--email", help="the writer account to sign in as; the password "
                   "is read from BLOOM_PASSWORD")
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
    duplicates = sorted({g for g, n in Counter(names).items() if n > 1})
    if duplicates:
        raise IngestError(
            f"{len(duplicates)} gene names appear more than once, and would "
            f"share one object: {', '.join(duplicates[:5])}"
        )

    # Re-sliced by column once, so taking one gene's vector is cheap. The
    # matrix arrives row-major, where every gene read would touch every row.
    matrix = adata.X
    by_gene = matrix.tocsc() if hasattr(matrix, "tocsc") else np.asarray(matrix)
    _check_finite(by_gene, names)

    for gene, expected in expectations.items():
        if gene not in names:
            raise IngestError(
                f"--expect-nonzero names {gene!r}, which is not in this file"
            )
        actual = len(gene_counts(by_gene, names.index(gene)))
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


def _check_finite(by_gene, names: list[str]) -> None:
    """Refuse NaN and infinity: the explorer cannot colour a cell by either, and
    JSON has no spelling for them."""
    import numpy as np

    if hasattr(by_gene, "indptr"):
        bad = ~np.isfinite(by_gene.data)
        columns = np.repeat(np.arange(by_gene.shape[1]), np.diff(by_gene.indptr))[bad]
    else:
        columns = np.nonzero(~np.isfinite(by_gene))[1]
    if len(columns):
        genes = sorted({names[i] for i in columns})
        raise IngestError(f"{len(genes)} genes hold values that are not finite (NaN "
                          f"or infinite): {', '.join(genes[:5])}")


def gene_counts(by_gene, column: int) -> dict[str, float]:
    """One gene's value for every cell that has one, keyed by cell index.

    Single-cell expression is mostly zeros, so only the non-zero cells are
    stored and an absent index reads as zero. The index is
    `scrna_cells.cell_number` -- 0-based, in source-file order -- which is the
    order `scrna_cell_arrays` returns cells in.

    This is the shape every dataset already in the platform is stored in.
    """
    import numpy as np

    taken = by_gene[:, column]
    dense = (taken.toarray() if hasattr(taken, "toarray") else np.asarray(taken))
    dense = dense.ravel()
    nonzero = np.flatnonzero(dense)
    return {str(int(i)): float(dense[i]) for i in nonzero}


def check_dataset_name(name: str) -> None:
    """The dataset name is a path segment too. A slash would write the whole
    dataset under another prefix, possibly over another dataset's genes."""
    if not name.strip():
        raise IngestError("the dataset name is blank")
    if not ingest_api.dataset_name_ok(name.strip()):
        raise IngestError(
            f"dataset name {name!r} cannot be part of an object path; it may "
            f"hold letters, digits, spaces, and . _ -"
        )


def object_path(dataset_name: str, gene: str) -> str:
    return COUNTS_PATH.format(dataset=dataset_name.strip(), gene=gene)


def open_dataset(writer, name: str, species_id: int, barcodes: list[str]) -> int:
    """Find the finished dataset, and refuse unless it holds these cells in this
    order, compared barcode by barcode against what the cell loader stored."""
    found = ingest_api.find_dataset(writer, species_id, name)
    if found is None:
        raise IngestError(
            f"no dataset named {name.strip()!r} for species {species_id}. Load its "
            f"cells first with scripts/ingest_scrnaseq.py"
        )
    dataset_id = found["id"]
    if not found.get("ingested_at"):
        raise IngestError(
            f"dataset {dataset_id}'s cells are not finished. Finish them first by "
            f"running scripts/ingest_scrnaseq.py again"
        )
    stored = [r["barcode"] for r in ingest_api.read_all(
        writer, "scrna_cells", "cell_number,barcode",
        filters=[("eq", "dataset_id", dataset_id)], order="cell_number")]

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
    recorded = found.get("n_cells")
    if recorded is not None and recorded != len(barcodes):
        raise IngestError(
            f"dataset {dataset_id} records {recorded} cells but holds {len(stored)}"
        )
    return dataset_id


def _check_genes(dataset_id: int, rows: list[dict], names: list[str]) -> None:
    seen = Counter(r["gene_name"] for r in rows)
    twice = sorted(g for g, n in seen.items() if n > 1)
    if twice:
        raise IngestError(f"dataset {dataset_id} has {', '.join(twice[:5])} registered "
                          f"more than once; an admin has to remove the extra rows")
    position = {g: i for i, g in enumerate(names)}
    foreign = sorted(g for g in seen if g not in position)
    if foreign:
        raise IngestError(f"dataset {dataset_id} has {len(foreign)} genes registered "
                          f"that this file does not hold (e.g. {', '.join(foreign[:3])}); "
                          f"they do not describe the same dataset")
    for r in rows:
        if r["gene_number"] != position[r["gene_name"]]:
            raise IngestError(f"{r['gene_name']} is gene {r['gene_number']} in dataset "
                              f"{dataset_id} and {position[r['gene_name']]} in this "
                              f"file; they do not describe the same dataset")


def register_genes(writer, dataset_id: int, names: list[str]) -> dict[str, int]:
    """Give every gene a row numbered by its position in the file, and hand back
    their ids. A re-run lands on the rows the first run made."""
    def stored():
        return ingest_api.read_all(writer, "scrna_genes", "id,gene_number,gene_name",
                                   filters=[("eq", "dataset_id", dataset_id)])

    rows = stored()
    _check_genes(dataset_id, rows, names)
    known = {r["gene_name"] for r in rows}
    missing = [(i, g) for i, g in enumerate(names) if g not in known]
    for start in range(0, len(missing), GENE_BATCH):
        chunk = missing[start:start + GENE_BATCH]
        ingest_api.insert(writer, f"register genes {chunk[0][0]}–{chunk[-1][0]}",
                          "scrna_genes", [{"dataset_id": dataset_id, "gene_number": i,
                                           "gene_name": g} for i, g in chunk])
    if missing:
        rows = stored()
        _check_genes(dataset_id, rows, names)
    if len(rows) != len(names):
        raise IngestError(f"dataset {dataset_id} has {len(rows)} genes registered but "
                          f"the file holds {len(names)}")
    return {r["gene_name"]: r["id"] for r in rows}


def already_written(writer, dataset_id: int, gene_ids: dict[str, int]) -> set[str]:
    """Genes whose object is already recorded, so a re-run can skip them.

    The object is written before its row, so a run stopped between the two
    leaves an object nothing points at, which the next run uploads again.
    """
    rows = ingest_api.read_all(writer, "scrna_counts", "id,gene_id",
                               filters=[("eq", "dataset_id", dataset_id)])
    name_of = {v: k for k, v in gene_ids.items()}
    recorded = Counter(r["gene_id"] for r in rows)
    twice = sorted(name_of.get(g, str(g)) for g, n in recorded.items() if n > 1)
    if twice:
        raise IngestError(f"dataset {dataset_id} records {', '.join(twice[:5])} more "
                          f"than once; an admin has to remove the extra rows")
    return {name_of[g] for g in recorded if g in name_of}


def upload(writer, path: str, payload: bytes) -> None:
    """Put one gene's vector in the bucket, replacing whatever was there, so a
    re-run repairs an object from a stopped one. The storage handle is taken per
    request, from the current session."""
    writer.write(f"upload {path}", lambda client: client.storage.from_(BUCKET).upload(
        path=path, file=payload,
        file_options={"content-type": "application/json", "upsert": "true"}))


def write_counts(writer, dataset_id: int, dataset_name: str, genes: dict,
                 gene_ids: dict[str, int], done: set[str]) -> tuple[int, int]:
    """Write every gene not already recorded. Returns written and skipped."""
    pending: list[dict] = []
    written = 0
    for number, gene in enumerate(genes["names"]):
        if gene in done:
            continue
        path = object_path(dataset_name, gene)
        upload(writer, path, json.dumps(gene_counts(genes["by_gene"], number)).encode())
        pending.append({"dataset_id": dataset_id, "gene_id": gene_ids[gene],
                        "counts_object_path": path})
        written += 1
        if len(pending) >= RECORD_BATCH:
            _record(writer, pending)
            pending = []
    if pending:
        _record(writer, pending)
    return written, len(done)


def _record(writer, rows: list[dict]) -> None:
    ingest_api.insert(writer, f"record {len(rows)} genes", "scrna_counts", rows)


def load(writer, name: str, species_id: int, genes: dict) -> tuple[int, int, int]:
    """Register the genes and write what is missing. Returns the dataset id and
    how many genes were written and skipped."""
    check_dataset_name(name)
    dataset_id = open_dataset(writer, name, species_id, genes["barcodes"])
    gene_ids = register_genes(writer, dataset_id, genes["names"])
    done = already_written(writer, dataset_id, gene_ids)
    written, skipped = write_counts(writer, dataset_id, name, genes, gene_ids, done)
    return dataset_id, written, skipped


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

    name = args.dataset_name.strip()
    if args.dry_run:
        print(f"{args.h5ad.name}: {len(genes['names'])} genes over "
              f"{genes['n_cells']} cells, every name usable as an object path and "
              f"every value finite")
        # Say which expectations were met, so a flag that was mistyped into
        # doing nothing is visible rather than reading as a pass.
        for gene, count in sorted(expectations.items()):
            print(f"  {gene} is non-zero in {count} cells, as expected")
        prefix = COUNTS_PATH.split("{gene}")[0].format(dataset=name)
        print(f"would write {len(genes['names'])} objects under {prefix} and a row "
              f"for each")
        print("dry run — nothing written")
        return 0

    if not args.email:
        print("refusing to ingest: --email names the account to sign in as; its "
              "password is read from BLOOM_PASSWORD", file=sys.stderr)
        return 1

    marker = ingest_api.Marker(ingest_api.marker_path(args.h5ad, name))
    try:
        marker.check()
        password = ingest_api.read_password()
        api_url, anon_key = ingest_api.resolve_api(args.server, args.api_url, args.anon_key)
        session = ingest_api.sign_in(api_url, anon_key, args.email, password)
        dataset_id, written, skipped = load(ingest_api.Writer(session, marker),
                                            args.dataset_name, args.species_id, genes)
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1

    if written == 0 and skipped == len(genes["names"]):
        print(f"dataset {dataset_id} is already loaded: {skipped} genes over "
              f"{genes['n_cells']} cells")
    else:
        print(summarise(genes, dataset_id, written, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
