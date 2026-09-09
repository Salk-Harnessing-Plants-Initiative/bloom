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
        --expect-cells 8683

Re-running replaces that dataset's cells and catalogue. Because it is one
transaction, an interrupted run rolls back and can simply be run again.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# The palette the explorer already uses for cluster colours. Assigning here
# rather than in the browser is what keeps a cell type the same colour across
# reloads and between users.
PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
    "#86BCB6", "#F1CE63", "#D37295", "#A0CBE8", "#FFBE7D",
    "#8CD17D", "#B6992D", "#499894", "#FABFD2", "#D4A6C8",
    "#79706E", "#D7B5A6", "#6B4C9A",
]

# Below these the neighbour check cannot separate a real embedding from a
# shuffled one, so it is skipped rather than guessed at.
MIN_CELLS_FOR_ALIGNMENT = 50
MAX_CHANCE_FOR_ALIGNMENT = 0.25

# scrna_clusters.ordinal is a SMALLINT the browser packs into a Uint8Array, and
# 255 is the explorer's orphan sentinel, so a catalogue may hold 0..254.
MAX_ORDINAL = 255

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
    coords = np.asarray(adata.obsm[umap_key])
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
    levels = sorted(set(labels))
    if len(levels) > MAX_ORDINAL:
        raise IngestError(
            f"obs[{annotation!r}] has {len(levels)} levels; the explorer packs "
            f"the ordinal into a byte and reserves {MAX_ORDINAL} for orphans"
        )

    chance = sum(c * c for c in Counter(labels).values()) / (len(labels) ** 2)
    # Below these it cannot tell a real embedding from a shuffled one: with two
    # cell types a coin flip already scores 0.5.
    purity = None
    if adata.n_obs >= MIN_CELLS_FOR_ALIGNMENT and chance <= MAX_CHANCE_FOR_ALIGNMENT:
        purity = neighbour_purity(coords, labels)
        if purity < chance * 2:
            raise IngestError(
                f"cells of the same type are not near each other in "
                f"obsm[{umap_key!r}] — neighbours share a label {purity:.3f} of "
                f"the time against {chance:.3f} expected by chance. The "
                f"coordinates likely do not line up with these cells row for row."
            )

    return {
        "n_cells": int(adata.n_obs),
        "purity": purity,
        "n_genes": int(adata.n_vars),
        "x": [float(v) for v in coords[:, 0]],
        "y": [float(v) for v in coords[:, 1]],
        "labels": labels,
        "samples": samples,
        "levels": levels,
        "barcodes": [str(v) for v in adata.obs_names],
    }


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
    blank = sum(1 for v in text if not v.strip())
    if blank:
        raise IngestError(
            f"obs[{column!r}] has {blank} blank value(s); every cell needs a name"
        )
    return text


def neighbour_purity(coords, labels: list[str], k: int = 15) -> float:
    """How often a cell's nearest neighbours share its label.

    Coordinates and cell types arrive from two places -- for a joint embedding,
    the coordinates are a slice out of a much larger array that someone lines up
    by hand. If that slice is wrong, every check above still passes and the plot
    looks entirely plausible. Cells of a type cluster together in a real
    embedding, so a misaligned one scores at chance.
    """
    import numpy as _np

    n = len(labels)
    k = min(k, n - 1)
    if k < 1:
        return 1.0
    codes = _np.unique(_np.asarray(labels), return_inverse=True)[1]
    hits = 0
    # Chunked so an 8,683-cell distance matrix never exists all at once.
    for start in range(0, n, 512):
        block = coords[start:start + 512]
        d = ((block[:, None, :] - coords[None, :, :]) ** 2).sum(-1)
        d[_np.arange(len(block)), _np.arange(start, start + len(block))] = _np.inf
        nearest = _np.argpartition(d, k, axis=1)[:, :k]
        hits += int((codes[nearest] == codes[start:start + len(block), None]).sum())
    return hits / (n * k)


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
        + (f"neighbours share a cell type {cells['purity']:.3f} of the time\n  "
           if cells["purity"] is not None else "")
        + f"samples: "
        + ", ".join(f"{k} {v}" for k, v in sorted(per_sample.items()))
    )


def load(conn, name: str, species_id: int, cells: dict, source_checksum: str,
         units: str) -> tuple[int, int]:
    """Write the dataset, its catalogue and its cells in one transaction.

    Order matters twice over. `scrna_cells` references the catalogue with
    ON DELETE RESTRICT, so the cells go first or the catalogue delete is refused
    on every run after the first. And the dataset's counts and checksum are
    written last, after reading back what actually landed, so an interrupted run
    leaves stale provenance rather than a row attesting to a file it does not
    hold.

    Returns the dataset id and the number of cells actually stored.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM public.scrna_datasets "
            "WHERE name = %s AND species_id = %s AND deleted_at IS NULL",
            (name, species_id),
        )
        found = cur.fetchall()
        if len(found) > 1:
            raise IngestError(
                f"{len(found)} datasets are named {name!r} for species "
                f"{species_id}; cannot tell which to replace"
            )

        if found:
            dataset_id = found[0][0]
            cur.execute(
                "SELECT count(*) FROM public.scrna_cluster_stats WHERE dataset_id = %s",
                (dataset_id,),
            )
            if cur.fetchone()[0]:
                # scrna_cluster_stats and scrna_cluster_neighbors cascade off the
                # catalogue, so replacing it would discard the sidebar's counts,
                # centroids and marker lists with nothing here to rebuild them.
                raise IngestError(
                    f"dataset {dataset_id} has per-cluster statistics that "
                    f"replacing its catalogue would delete. Remove them "
                    f"deliberately first, then re-run."
                )
        else:
            cur.execute(
                "INSERT INTO public.scrna_datasets (name, species_id) "
                "VALUES (%s, %s) RETURNING id",
                (name, species_id),
            )
            dataset_id = cur.fetchone()[0]

        cur.execute("DELETE FROM public.scrna_cells WHERE dataset_id = %s", (dataset_id,))
        cur.execute("DELETE FROM public.scrna_clusters WHERE dataset_id = %s", (dataset_id,))

        cur.executemany(
            "INSERT INTO public.scrna_clusters "
            "(dataset_id, cluster_id, ordinal, name, color) VALUES (%s, %s, %s, %s, %s)",
            [
                (dataset_id, level, ordinal, level, PALETTE[ordinal % len(PALETTE)])
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
            "source_checksum = %s, ingested_at = %s, expression_units = %s "
            "WHERE id = %s",
            (cells["n_cells"], cells["n_genes"], source_checksum,
             datetime.now(timezone.utc), units, dataset_id),
        )
    return dataset_id, stored


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
            "is one transaction. The role needs INSERT and DELETE on "
            "scrna_datasets, scrna_clusters and scrna_cells.",
            file=sys.stderr,
        )
        return 1

    import psycopg

    try:
        with psycopg.connect(database_url) as conn:
            dataset_id, stored = load(
                conn, args.dataset_name, args.species_id, cells,
                checksum(args.h5ad), args.expression_units,
            )
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1

    print(f"loaded {stored} cells into dataset {dataset_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
