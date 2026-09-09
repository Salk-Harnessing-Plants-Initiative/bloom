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

Run deliberately against a chosen database, never as part of a migration:

    SUPABASE_URL=... SUPABASE_KEY=... \
      uv run --with anndata --with supabase python scripts/ingest_scrnaseq.py \
        --h5ad myb41_joint_SATURN_LABELS.h5ad \
        --dataset-name "MYB41 transgene" \
        --species-id 1 \
        --annotation nn_label_plain \
        --expect-cells 8683

Re-running replaces the dataset's cells and catalogue rather than adding to them,
so a failed load can simply be run again.
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
    "#79706E", "#D7B5A6", "#59A14F",
]

# scrna_clusters.ordinal is a SMALLINT the browser packs into a Uint8Array, and
# 255 is the explorer's orphan sentinel, so a catalogue may hold 0..254.
MAX_ORDINAL = 255

# Postgres rejects an INSERT far larger than this in one request.
BATCH_ROWS = 5000


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

    if not h5ad_path.exists():
        raise IngestError(f"no such file: {h5ad_path}")

    adata = anndata.read_h5ad(h5ad_path)

    if umap_key not in adata.obsm:
        raise IngestError(
            f"{h5ad_path.name} has no obsm[{umap_key!r}] — the explorer plots "
            f"stored coordinates and never computes them. Found: "
            f"{sorted(adata.obsm) or 'nothing'}"
        )
    # anndata guarantees obsm rows match n_obs, so a row-count check here would
    # be unreachable; the dimension is not guaranteed.
    coords = adata.obsm[umap_key]
    if coords.shape[1] < 2:
        raise IngestError(
            f"obsm[{umap_key!r}] has {coords.shape[1]} dimension(s), need 2"
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

    labels = [str(v) for v in adata.obs[annotation]]
    samples = [str(v) for v in adata.obs[sample_column]]
    levels = sorted(set(labels))
    if len(levels) > MAX_ORDINAL:
        raise IngestError(
            f"obs[{annotation!r}] has {len(levels)} levels; the explorer packs "
            f"the ordinal into a byte and reserves {MAX_ORDINAL} for orphans"
        )

    return {
        "n_cells": int(adata.n_obs),
        "n_genes": int(adata.n_vars),
        "x": [float(v) for v in coords[:, 0]],
        "y": [float(v) for v in coords[:, 1]],
        "labels": labels,
        "samples": samples,
        "levels": levels,
        "barcodes": [str(v) for v in adata.obs_names],
    }


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
        f"{len(cells['levels'])} cell types\n  samples: "
        + ", ".join(f"{k} {v}" for k, v in sorted(per_sample.items()))
    )


def register_dataset(client, name: str, species_id: int, cells: dict,
                     source_checksum: str) -> int:
    """Create or update the dataset row and return its id."""
    existing = (
        client.table("scrna_datasets")
        .select("id")
        .eq("name", name)
        .execute()
        .data
    )
    row = {
        "name": name,
        "species_id": species_id,
        "n_cells": cells["n_cells"],
        "n_genes": cells["n_genes"],
        "source_checksum": source_checksum,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        # What a colourbar would be labelling. The cells carry no expression
        # values, so this describes the matrix the coordinates came from.
        "expression_units": "log1p normalised counts",
    }
    if existing:
        dataset_id = existing[0]["id"]
        client.table("scrna_datasets").update(row).eq("id", dataset_id).execute()
        return dataset_id
    return client.table("scrna_datasets").insert(row).execute().data[0]["id"]


def write_catalogue(client, dataset_id: int, levels: list[str]) -> None:
    """One row per cell type, with a stable ordinal and colour.

    Ordinals follow the sorted label order so the same file always produces the
    same catalogue, and a level beyond the palette repeats a colour rather than
    failing the load.
    """
    client.table("scrna_clusters").delete().eq("dataset_id", dataset_id).execute()
    client.table("scrna_clusters").insert([
        {
            "dataset_id": dataset_id,
            "cluster_id": level,
            "ordinal": ordinal,
            "name": level,
            "color": PALETTE[ordinal % len(PALETTE)],
        }
        for ordinal, level in enumerate(levels)
    ]).execute()


def write_cells(client, dataset_id: int, cells: dict) -> None:
    """One row per cell, in file order.

    `cell_number` carries that order because the RPC sorts on it, so the browser
    receives coordinates in the same sequence every time.
    """
    client.table("scrna_cells").delete().eq("dataset_id", dataset_id).execute()
    rows = [
        {
            "dataset_id": dataset_id,
            "cell_number": i,
            "barcode": barcode,
            "x": x,
            "y": y,
            "cluster_id": label,
            "replicate": sample,
        }
        for i, (barcode, x, y, label, sample) in enumerate(
            zip(cells["barcodes"], cells["x"], cells["y"],
                cells["labels"], cells["samples"])
        )
    ]
    for start in range(0, len(rows), BATCH_ROWS):
        client.table("scrna_cells").insert(rows[start:start + BATCH_ROWS]).execute()


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

    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_KEY are required", file=sys.stderr)
        return 1

    from supabase import create_client

    client = create_client(url, key)
    dataset_id = register_dataset(
        client, args.dataset_name, args.species_id, cells, checksum(args.h5ad)
    )
    write_catalogue(client, dataset_id, cells["levels"])
    write_cells(client, dataset_id, cells)
    print(f"loaded into dataset {dataset_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
