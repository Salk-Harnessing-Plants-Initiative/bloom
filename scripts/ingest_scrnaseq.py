#!/usr/bin/env python3
"""
Load a single-cell dataset's cells into the expression explorer.

Reads an `.h5ad` and writes three tables: `scrna_datasets` (the registration),
`scrna_clusters` (the cell-type catalogue, one row per label with a stable
ordinal and colour) and `scrna_cells` (one row per cell: its UMAP coordinates,
its cell type, and which sample it came from).

Nothing here touches object storage. The explorer's `scrna_cell_arrays` RPC
selects straight from `scrna_cells` joined to `scrna_clusters`, ordered by
`cell_number`, so the coordinates have to be columns. Per-gene expression is the
part that lives in storage, and it is loaded separately.

Run deliberately against a chosen database, never as part of a migration. The
whole load is one transaction over a direct connection, so a failure leaves the
dataset exactly as it was:

    DATABASE_URL=postgresql://user:pass@host:5432/postgres \
      uv run --with anndata --with 'psycopg[binary]' python scripts/ingest_scrnaseq.py \
        --h5ad myb41_joint_SATURN_LABELS.h5ad \
        --dataset-name "MYB41 transgene" \
        --species-id 1 \
        --annotation nn_label_plain \
        --expect-cells 8683 \
        --create

Re-running replaces that dataset's cells and catalogue, and needs no --create:
that flag guards registration only, so a mistyped name is refused rather than
loaded as a second copy alongside the real one. Because it is one transaction,
an interrupted run rolls back and can simply be run again.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Cluster colours. Assigning them here rather than in the browser is what keeps
# a cell type the same colour between users, and a reload keeps the colour and
# the name each surviving cell type already had. This supersedes the 20-colour
# list in scripts/backfill_scrna_cluster_colors.sql, which cannot cover the 23
# cell types this dataset carries.
PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
    "#86BCB6", "#F1CE63", "#D37295", "#A0CBE8", "#FFBE7D",
    "#8CD17D", "#B6992D", "#499894", "#FABFD2", "#D4A6C8",
    "#79706E", "#D7B5A6", "#6B4C9A",
]

# A real embedding gives essentially every cell its own point: on this dataset's
# 8,683 cells and on the 138,865-row joint embedding, every single point is
# distinct. So cells stacked on one point mean the obsm was allocated and never
# filled -- every value zero, which is finite, two-dimensional and the right
# length, so nothing else here notices, and the plot is a single dot.
MAX_DUPLICATE_POINT_SHARE = 0.001

# What a missing value looks like once something upstream has called astype(str)
# on it. Stored as-is, each of these becomes a real cell type in the legend, or a
# barcode that names no cell.
NOT_A_VALUE = {"", "nan", "none", "na", "<na>", "null"}

# scrna_cell_arrays casts x and y to REAL on the way out, so a coordinate above
# this stores fine and then fails for every reader of the dataset.
FLOAT32_MAX = 3.4028235e38


class IngestError(RuntimeError):
    """Something about the file or the database makes this load unsafe."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--h5ad", type=Path, required=True)
    p.add_argument("--dataset-name", required=True)
    p.add_argument("--species-id", type=int, required=True)
    p.add_argument(
        "--annotation",
        required=True,
        help="the obs column holding cell types; must be the one the "
             "differential expression was computed on, or the DE panel's cell "
             "types will not exist in the catalogue",
    )
    p.add_argument("--sample-column", default="sample")
    p.add_argument("--umap-key", default="X_umap")
    p.add_argument(
        "--expect-cells",
        type=int,
        help="refuse the load unless the file holds exactly this many cells",
    )
    p.add_argument(
        "--expression-units",
        default="log1p normalised counts",
        help="what the stored expression values are, for the colourbar label",
    )
    p.add_argument(
        "--create",
        action="store_true",
        help="register the dataset if no dataset of this name exists for this "
             "species. Without it an unrecognised name is refused, so a typo "
             "cannot load a second copy alongside the real one",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="read and check the file, write nothing")
    return p.parse_args(argv)


def read_cells(
    h5ad_path: Path,
    annotation: str,
    sample_column: str,
    umap_key: str,
    expect_cells: int | None,
) -> dict:
    """Pull everything the explorer needs out of the file, or refuse.

    Every check here runs before a single row is written, because a dataset that
    is half loaded looks to the explorer exactly like one that is complete.

    The coordinates are taken as given. They come out of the same file as the
    labels, in the row order anndata keeps them in, so nothing here can pair
    them up wrongly -- and whether the embedding itself is any good is the
    analysis's business, not this script's. The only thing refused about the
    embedding itself is an obsm that was never filled in, which is not a
    judgement about it but the absence of one. The rest of the coordinate
    checks are about shape and storability.
    """
    import anndata
    import numpy as np

    if not h5ad_path.exists():
        raise IngestError(f"no such file: {h5ad_path}")

    adata = anndata.read_h5ad(h5ad_path)

    if adata.n_obs == 0:
        raise IngestError(f"{h5ad_path.name} holds no cells")

    if umap_key not in adata.obsm:
        raise IngestError(
            f"{h5ad_path.name} has no obsm[{umap_key!r}] — the explorer plots "
            f"stored coordinates and never computes them. Found: "
            f"{sorted(adata.obsm) or 'nothing'}"
        )
    # anndata guarantees obsm rows match n_obs, so a row-count check here would
    # be unreachable; nothing else about the array is guaranteed.
    try:
        coords = np.asarray(adata.obsm[umap_key], dtype=float)
    except (TypeError, ValueError) as exc:
        raise IngestError(
            f"obsm[{umap_key!r}] does not read as numbers: {exc}"
        ) from exc
    if coords.ndim != 2:
        raise IngestError(
            f"obsm[{umap_key!r}] is {coords.ndim}-dimensional, need a 2-D array"
        )
    if coords.shape[1] != 2:
        # Not "at least 2": a 50-column X_pca would otherwise load as a UMAP,
        # which is the obvious workaround when the real coordinates are missing
        # and produces a plot nothing downstream can tell from the real thing.
        raise IngestError(
            f"obsm[{umap_key!r}] has {coords.shape[1]} dimensions, need exactly "
            f"2 — this looks like an embedding the explorer cannot plot"
        )
    if not np.isfinite(coords).all():
        raise IngestError(
            f"obsm[{umap_key!r}] holds values that are not finite; a single "
            f"infinity collapses the whole plot to one point"
        )
    if np.abs(coords).max() > FLOAT32_MAX:
        # The column is double precision, so this stores; the explorer's own
        # query casts it to REAL, so it breaks at read time for everyone.
        raise IngestError(
            f"obsm[{umap_key!r}] holds coordinates too large to store; the "
            f"largest is {np.abs(coords).max():.3g}"
        )
    _, piles = np.unique(coords, axis=0, return_counts=True)
    if piles.max() > max(1, len(coords) * MAX_DUPLICATE_POINT_SHARE):
        # An obsm allocated and never filled passes every other check here --
        # zeros are finite, two-dimensional and the right length -- and draws
        # every cell of the dataset as one dot.
        raise IngestError(
            f"obsm[{umap_key!r}] puts {piles.max()} of {len(coords)} cells on a "
            f"single point. Real coordinates give essentially every cell its "
            f"own; this array is unfilled, partly unfilled, or rounded so "
            f"coarsely that cells collide"
        )

    for column in (annotation, sample_column):
        if column not in adata.obs:
            raise IngestError(
                f"{h5ad_path.name} has no obs[{column!r}]. Found: "
                f"{', '.join(sorted(adata.obs.columns))}"
            )

    if expect_cells is not None and adata.n_obs != expect_cells:
        raise IngestError(
            f"expected {expect_cells} cells, file holds {adata.n_obs}"
        )

    labels = _text_column(adata, annotation)
    samples = _text_column(adata, sample_column)
    barcodes = _barcodes(adata)
    levels = sorted(set(labels))
    if len(levels) > len(PALETTE):
        # The palette binds long before the browser does -- it packs the ordinal
        # into a byte and reserves 255 for orphans, so 254 would fit. Wrapping
        # the palette instead would render two cell types identically in both
        # the plot and the legend, which is the defect this list replaced.
        raise IngestError(
            f"obs[{annotation!r}] has {len(levels)} cell types and there are "
            f"{len(PALETTE)} colours to tell them apart; two would be drawn "
            f"identically"
        )

    return {
        "n_cells": int(adata.n_obs),
        "n_genes": int(adata.n_vars),
        "x": [float(v) for v in coords[:, 0]],
        "y": [float(v) for v in coords[:, 1]],
        "labels": labels,
        "samples": samples,
        "levels": levels,
        "barcodes": barcodes,
    }


def _barcodes(adata) -> list[str]:
    """Read the cell barcodes, refusing anything that cannot identify a cell.

    Coordinates and labels come out of one file here, so nothing downstream has
    to join on the barcode -- but it is the only identifier a cell carries, and
    the recovery path when they ever do arrive separately is a join on it, not
    on position. Duplicates make that join ambiguous with nothing recording that
    it ever was. anndata.concat leaves 10x barcodes repeated across samples
    unless it is given index_unique, and warns only at concat time.
    """
    text = [str(v).strip() for v in adata.obs_names]
    blank = sum(1 for v in text if v.lower() in NOT_A_VALUE)
    if blank:
        raise IngestError(
            f"{blank} of {len(text)} cells have no barcode; every cell needs one"
        )
    repeated = [b for b, n in Counter(text).items() if n > 1]
    if repeated:
        shown = ", ".join(repr(b) for b in sorted(repeated)[:3])
        raise IngestError(
            f"{len(text) - len(set(text))} of {len(text)} barcodes are "
            f"duplicates ({len(repeated)} repeated, e.g. {shown}). Cells "
            f"concatenated without index_unique do this; a barcode has to name "
            f"one cell"
        )
    return text


def _text_column(adata, column: str) -> list[str]:
    """Read a column as text, refusing anything that is not a usable label.

    A missing value would otherwise become the string "nan" and be stored as a
    real cell type -- one that merges with any genuine level of that spelling and
    appears in the legend as biology.
    """
    values = adata.obs[column]
    missing = int(values.isna().sum())
    if missing:
        raise IngestError(
            f"obs[{column!r}] has {missing} missing value(s); every cell needs one"
        )
    text = [str(v) for v in values]
    blank = sum(1 for v in text if v.strip().lower() in NOT_A_VALUE)
    if blank:
        raise IngestError(
            f"obs[{column!r}] has {blank} value(s) that are blank or read as a "
            f"missing value; every cell needs a name"
        )
    return text


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summarise(cells: dict) -> str:
    per_sample = Counter(cells["samples"])
    return (
        f"{cells['n_cells']} cells, {cells['n_genes']} genes, "
        f"{len(cells['levels'])} cell types\n  "
        + "samples: "
        + ", ".join(f"{k} {v}" for k, v in sorted(per_sample.items()))
    )


def load(conn, name: str, species_id: int, cells: dict, source_checksum: str,
         units: str, annotation: str, create: bool = False) -> tuple[int, int, bool]:
    """Write the dataset, its catalogue and its cells in one transaction.

    Order matters twice over. `scrna_cells` references the catalogue with
    ON DELETE RESTRICT, so the cells go first or the catalogue delete is refused
    on every run after the first. And the dataset's counts and checksum are
    written last, after reading back what actually landed, so the row can never
    attest to a file it does not hold.

    A reload is refused outright when the dataset already has rows that name a
    cell type or a cell position, since nothing here can rebuild them.

    Registering a dataset that does not exist yet takes `create`, so a mistyped
    name is refused rather than quietly loaded as a second copy.

    Returns the dataset id, the number of cells actually stored, and whether the
    dataset was registered by this call rather than replaced.
    """
    name = name.strip()
    if not name:
        raise IngestError("the dataset name is blank")
    if len(cells["levels"]) > len(PALETTE):
        raise IngestError(
            f"{len(cells['levels'])} cell types and {len(PALETTE)} colours to "
            f"tell them apart; two would be drawn identically"
        )
    with conn.cursor() as cur:
        cur.execute(
            # btrim on the column too: rows registered before the name was
            # trimmed here can carry padding, and an exact match would miss
            # them and offer to register a second copy.
            "SELECT id FROM public.scrna_datasets "
            "WHERE btrim(name) = %s AND species_id = %s AND deleted_at IS NULL",
            (name, species_id),
        )
        found = cur.fetchall()
        if len(found) > 1:
            raise IngestError(
                f"{len(found)} datasets are named {name!r} for species "
                f"{species_id}; cannot tell which to replace"
            )

        created = not found
        if found and create:
            raise IngestError(
                f"dataset {found[0][0]} is already named {name!r} for species "
                f"{species_id}. Re-run without --create to replace its cells; "
                f"--create is for registering a dataset that does not exist yet"
            )
        if found:
            dataset_id = found[0][0]
            cur.execute(
                "SELECT"
                " (SELECT count(*) FROM public.scrna_cluster_stats WHERE dataset_id = %(d)s),"
                " (SELECT count(*) FROM public.scrna_cluster_neighbors WHERE dataset_id = %(d)s),"
                " (SELECT count(*) FROM public.scrna_counts WHERE dataset_id = %(d)s),"
                " (SELECT count(*) FROM public.scrna_de WHERE dataset_id = %(d)s)",
                {"d": dataset_id},
            )
            # Anything keyed by cell-type name or by cell position blocks a
            # reload. The first two cascade off the catalogue; the counts name
            # files read by cell position, which renumbering shifts; the
            # differential expression rows name cell types and have no foreign
            # key to the catalogue, so a changed cell-type set leaves them
            # naming types that no longer exist.
            blocked = [
                what
                for what, n in zip(
                    ("per-cluster statistics", "neighbour rows",
                     "per-gene expression rows",
                     "differential expression rows"),
                    cur.fetchone(),
                )
                if n
            ]
            if blocked:
                raise IngestError(
                    f"dataset {dataset_id} has {' and '.join(blocked)} that "
                    f"reloading its cells would invalidate. Remove them "
                    f"deliberately first, then re-run."
                )
        else:
            # Creating on a miss is how a mistyped name forks a dataset: the
            # load succeeds, reports the same sentence a replace does, and the
            # next run with the name spelled right finds two and refuses every
            # time after. Count files are keyed by dataset name, so the copies
            # would share a namespace too.
            if not create:
                raise IngestError(
                    f"no dataset named {name!r} for species {species_id}. Pass "
                    f"--create to register a new one; without it a mistyped "
                    f"name would silently load a second copy"
                )
            cur.execute(
                "INSERT INTO public.scrna_datasets (name, species_id) "
                "VALUES (%s, %s) RETURNING id",
                (name, species_id),
            )
            dataset_id = cur.fetchone()[0]

        # Cluster names and colours are edited by hand after a load -- the
        # backfill script seeds them and says to fix the biology in Studio -- and
        # they cannot be rebuilt from the file, so a surviving cell type keeps
        # both. New cell types take a colour no surviving one is already using.
        cur.execute(
            "SELECT cluster_id, name, color FROM public.scrna_clusters "
            "WHERE dataset_id = %s", (dataset_id,),
        )
        kept = {
            cid: (name, color) for cid, name, color in cur.fetchall()
            if cid in set(cells["levels"])
        }
        taken = {color.lower() for _, color in kept.values() if color}
        spare = iter([c for c in PALETTE if c.lower() not in taken])

        cur.execute("DELETE FROM public.scrna_cells WHERE dataset_id = %s", (dataset_id,))
        cur.execute("DELETE FROM public.scrna_clusters WHERE dataset_id = %s", (dataset_id,))

        cur.executemany(
            "INSERT INTO public.scrna_clusters "
            "(dataset_id, cluster_id, ordinal, name, color) VALUES (%s, %s, %s, %s, %s)",
            [
                (dataset_id, level, ordinal,
                 (kept.get(level, (None, None))[0] or "").strip() or level,
                 (kept.get(level, (None, None))[1] or "").strip() or next(spare))
                for ordinal, level in enumerate(cells["levels"])
            ],
        )
        cur.executemany(
            "INSERT INTO public.scrna_cells "
            "(dataset_id, cell_number, barcode, x, y, cluster_id, replicate) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (dataset_id, i, barcode, x, y, label, sample)
                for i, (barcode, x, y, label, sample) in enumerate(
                    zip(cells["barcodes"], cells["x"], cells["y"],
                        cells["labels"], cells["samples"])
                )
            ],
        )

        cur.execute(
            "SELECT count(*) FROM public.scrna_cells WHERE dataset_id = %s", (dataset_id,)
        )
        stored = cur.fetchone()[0]
        if stored != cells["n_cells"]:
            raise IngestError(
                f"wrote {stored} cells but the file holds {cells['n_cells']}"
            )

        cur.execute(
            "UPDATE public.scrna_datasets SET n_cells = %s, n_genes = %s, "
            "source_checksum = %s, ingested_at = %s, expression_units = %s, "
            "metadata = COALESCE(metadata, '{}'::jsonb) "
            "  || jsonb_build_object('cell_type_column', %s::text) "
            "WHERE id = %s",
            (cells["n_cells"], cells["n_genes"], source_checksum,
             datetime.now(timezone.utc), units, annotation, dataset_id),
        )
    return dataset_id, stored, created


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        cells = read_cells(
            args.h5ad, args.annotation, args.sample_column,
            args.umap_key, args.expect_cells,
        )
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1

    print(f"{args.h5ad.name}: {summarise(cells)}")

    if args.dry_run:
        print("dry run — nothing written")
        return 0

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print(
            "DATABASE_URL is required — a direct connection, so the whole load "
            "is one transaction. The role needs SELECT, INSERT and DELETE on "
            "scrna_clusters and scrna_cells, SELECT, INSERT and UPDATE on "
            "scrna_datasets, and SELECT on scrna_cluster_stats, "
            "scrna_cluster_neighbors, scrna_counts and scrna_de.",
            file=sys.stderr,
        )
        return 1

    import psycopg

    try:
        with psycopg.connect(database_url) as conn:
            dataset_id, stored, created = load(
                conn, args.dataset_name, args.species_id, cells,
                checksum(args.h5ad), args.expression_units, args.annotation,
                create=args.create,
            )
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        # A species id that is right on one database and wrong on another is the
        # likeliest operator mistake, and it arrives as a foreign key violation.
        print(f"the database refused the load: {exc}", file=sys.stderr)
        return 1

    # Which of the two happened, because they are the same sentence otherwise
    # and a mistyped name is exactly the case worth seeing.
    what = "registered" if created else "replaced the cells of"
    print(f"{what} dataset {dataset_id} ({args.dataset_name.strip()!r}): "
          f"{stored} cells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
