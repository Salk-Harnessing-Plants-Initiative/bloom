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
        --group-column nn_source \
        --expect-cells 8683

Re-running replaces that dataset's cells and catalogue. Because it is one
transaction, an interrupted run rolls back and can simply be run again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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

# Below these there is not enough to judge, so the check is skipped rather than
# guessed at. Two cell types are enough: measured on 900 well-separated cells,
# good coordinates score 1.00 and a shuffle about 0.00 at two, three and four,
# because scaling by the room above chance already handles one type dominating.
# A higher gate left a three-type dataset with no check at all.
MIN_CELLS_FOR_ALIGNMENT = 50
MIN_LEVELS_FOR_ALIGNMENT = 2

# A real embedding gives essentially every cell its own point: on this dataset's
# 8,683 cells and on the 138,865-row joint embedding, every single point is
# distinct. So more than one cell on a point, and above this share of them,
# means the array is partly or wholly unfilled -- which the neighbour check
# cannot catch, and reads as good alignment. See read_cells.
MAX_DUPLICATE_POINT_SHARE = 0.001

# scrna_cell_arrays casts x and y to REAL on the way out, so a coordinate above
# this stores fine and then fails for every reader of the dataset.
FLOAT32_MAX = 3.4028235e38

# How far neighbour agreement must sit from chance towards perfect. A ratio does
# not work: with one dominant cell type chance is already near 0.5, and twice
# that is beyond what any real embedding reaches.
#
# Set from measurement. On the first dataset, with the weakest legitimate
# coordinates available (its PCA, since the real embedding has not shipped yet):
# aligned scores 0.21 and 0.27 depending on the annotation, a shuffle of the same
# coordinates 0.00 either way. The bar sits half way down to a shuffle.
#
# Read what this does and does not catch in read_cells before relying on it.
MIN_ALIGNMENT_EXCESS = 0.10

# A facet becomes a row of toggles, so it has to be a handful of labels. Above
# this it is a measurement rather than something to filter by, and it would
# reach the browser as hundreds of buttons.
MAX_FACET_VALUES = 12


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
    p.add_argument(
        "--genotype-column",
        default="sample",
        help="the obs column naming which genotype each cell came from. Each "
             "distinct value becomes a genotype record the cells point at",
    )
    p.add_argument(
        "--control",
        metavar="GENOTYPE",
        help="which genotype is the control, named rather than guessed: a "
             "dataset whose wild type is not called Col-0 would be guessed wrong",
    )
    p.add_argument(
        "--construct",
        action="append",
        default=[],
        metavar="GENOTYPE=NAME",
        help="the construct a transgenic line carries. A property of the line, "
             "not of each cell; repeatable",
    )
    p.add_argument("--sample-column", default="sample")
    p.add_argument(
        "--group-column",
        action="append",
        default=[],
        metavar="COLUMN",
        help="an obs column whose groups were lined up against the coordinates "
             "separately, scored on its own as well as the file as a whole. The "
             "sample column always is; name the source column of a joint "
             "embedding here (repeatable). Name only columns that describe where "
             "the cells came from -- a biological column measures biology and "
             "will refuse a correct file",
    )
    p.add_argument(
        "--facet",
        action="append",
        default=[],
        metavar="COLUMN",
        help="an obs column to store on each cell so the explorer can show or "
             "hide those cells -- transgene status, for instance. A few short "
             "labels only; repeatable",
    )
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


def alignment(coords, labels: list[str]) -> tuple[float, float] | None:
    """How often neighbours share a cell type, and how far that sits from chance
    towards perfect.

    The second number is scaled by the room above chance, so the bar means the
    same thing whether cell types are balanced or not: one dominant type puts
    chance near 0.5 on its own, and a fixed score or a multiple of chance would
    then either pass everything or refuse real data.

    None when there are too few cells or too few cell types to tell a real
    embedding from a shuffled one.
    """
    if len(labels) < MIN_CELLS_FOR_ALIGNMENT:
        return None
    if len(set(labels)) < MIN_LEVELS_FOR_ALIGNMENT:
        return None
    chance = sum(c * c for c in Counter(labels).values()) / (len(labels) ** 2)
    purity = neighbour_purity(coords, labels)
    return purity, (purity - chance) / (1 - chance)


def read_facets(adata, columns: tuple[str, ...]) -> list[dict]:
    """Per-cell labels the explorer can filter on, one entry per named column.

    Refused rather than truncated when a column has too many values: a facet is
    a row of toggles, and a column with hundreds of them is a measurement
    someone has mistaken for a label.
    """
    out = []
    for column in columns:
        if column not in adata.obs:
            raise IngestError(
                f"no obs[{column!r}] to use as a facet. Found: "
                f"{', '.join(sorted(adata.obs.columns))}"
            )
        values = _text_column(adata, column)
        levels = sorted(set(values))
        if len(levels) > MAX_FACET_VALUES:
            raise IngestError(
                f"obs[{column!r}] has {len(levels)} values, more than the "
                f"{MAX_FACET_VALUES} a row of toggles can show; it looks like a "
                f"measurement rather than a label"
            )
        out.append({"column": column, "values": values, "levels": levels})
    return out


def _grouping_columns(adata, annotation: str, sample_column: str,
                      extra: tuple[str, ...]) -> list[str]:
    """Columns whose groups were each lined up against the coordinates
    separately, so each has to be scored on its own.

    The sample is always one. Anything else has to be named, because nothing here
    can tell a provenance column from a biological one, and scoring within a
    biological group measures biology: on the first dataset, cells in the
    meristem are undifferentiated, so their types genuinely overlap in the
    embedding and the group scores 0.088 with the coordinates perfectly correct.
    Sweeping every short column refused that dataset.

    The annotation is excluded either way -- the score already groups by it.
    """
    for column in extra:
        if column not in adata.obs:
            raise IngestError(
                f"no obs[{column!r}] to group by. Found: "
                f"{', '.join(sorted(adata.obs.columns))}"
            )
    return sorted(({sample_column} | set(extra)) - {annotation})


def _misaligned(umap_key: str, what: str, purity: float, excess: float,
                grouped: bool = False) -> str:
    return (
        f"cells of the same type are not near each other in obsm[{umap_key!r}] "
        f"for {what} — neighbours share a label {purity:.3f} of the time, only "
        f"{excess:.2f} of the way from chance to perfect. The coordinates likely "
        f"do not line up with these cells row for row."
        + (" If this column describes biology rather than where the cells came "
           "from, that is the more likely explanation: do not group by it."
           if grouped else "")
    )


def read_cells(
    h5ad_path: Path,
    annotation: str,
    sample_column: str,
    umap_key: str,
    expect_cells: int | None,
    group_columns: tuple[str, ...] = (),
    facet_columns: tuple[str, ...] = (),
) -> dict:
    """Pull everything the explorer needs out of the file, or refuse.

    Every check here runs before a single row is written, because a dataset that
    is half loaded looks to the explorer exactly like one that is complete.

    What the alignment check is worth, measured on the first dataset rather than
    argued. It catches unfilled coordinates, and misalignment affecting most of a
    group or most of the file: an off-by-any-amount row shift, and one group's
    rows reordered, are refused several times over. A wrong slice of one dataset
    inside a joint embedding is caught only if that source column is named with
    --group-column -- the summary prints which columns were scored, so a
    forgotten one is visible rather than silent.

    It is a bulk check, and two things follow. Damage scattered evenly is the
    hard case: a quarter to a third of rows can carry another cell's coordinates
    and still load -- a quarter on this dataset's `nn_label_plain`, a third on
    its `singler`, since the tolerance grows with how well the labels separate.
    Those are thresholds for the load, which needs the whole file and every group
    to clear the bar; the whole-file score alone tolerates rather more. Damage
    concentrated in one group is what the grouping catches, since that group falls
    to chance on its own while the average stays comfortable.

    And it is blind by construction to any misalignment that keeps every cell
    inside a group of its own cell type: a permutation within one type, one
    type's cells landing on another's cluster, or coordinates synthesised from
    the labels all score at least what a correct load scores. That last family is
    what lining two sides up by sorted cell type produces.

    So this is evidence, not proof, and the thing that actually keeps the rows
    together is that coordinates and labels come out of the same file. If they
    ever arrive separately they must be joined on the barcode, not by position.
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
        # An obsm allocated and never filled is the likeliest way to get bad
        # coordinates, and it defeats the alignment check below rather than
        # tripping it: with every distance tied, every cell gets the same
        # arbitrary neighbours, so the score climbs towards the largest cell
        # type's share instead of falling to chance. Measured on this file with
        # the dominant-class annotation, an all-zero array scores 0.298 against
        # the real embedding's 0.269.
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

    scored = alignment(coords, labels)
    purity = scored[0] if scored else None
    if scored and scored[1] < MIN_ALIGNMENT_EXCESS:
        raise IngestError(_misaligned(umap_key, "these cells", *scored))

    # One average over the whole file cannot see damage confined to part of it:
    # a third of the cells can carry another cell's coordinates and still clear
    # the bar. So each group is scored on its own as well -- every sample, and
    # whatever else --group-column names, such as the source dataset in a joint
    # object like this one.
    grouped = []
    for column in _grouping_columns(adata, annotation, sample_column,
                                    group_columns):
        judged = []
        for group in sorted(set(adata.obs[column].astype(str))):
            values = adata.obs[column].astype(str).to_numpy()
            rows = np.flatnonzero(values == group)
            scored = alignment(coords[rows], [labels[i] for i in rows])
            if scored:
                judged.append(scored[1])
            if scored and scored[1] < MIN_ALIGNMENT_EXCESS:
                raise IngestError(_misaligned(
                    umap_key, f"the cells with obs[{column!r}] == {group!r}",
                    *scored, grouped=column in group_columns,
                ))
        grouped.append((column, len(set(adata.obs[column].astype(str))), judged))

    return {
        "n_cells": int(adata.n_obs),
        "purity": purity,
        "grouped": grouped,
        "facets": read_facets(adata, facet_columns),
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
           if cells["purity"] is not None
           else "too few cells or cell types to check the coordinates line up\n  ")
        + f"samples: "
        + ", ".join(f"{k} {v}" for k, v in sorted(per_sample.items()))
        # Which groups were actually judged. Everything else here describes the
        # file; this describes what was checked, so a --group-column that was
        # forgotten, or named a column too sparse to score, is visible instead
        # of silently doing nothing.
        + "".join(
            f"\n  grouped by {column}: {len(judged)} of {n_groups} groups scored"
            + (f", weakest {min(judged):.3f} of the way from chance to perfect"
               if judged
               else " — none had both enough cells and enough cell types to judge")
            for column, n_groups, judged in cells["grouped"]
        )
        # Say what can be filtered on, so a --facet that named the wrong column
        # is visible rather than quietly producing nothing to click.
        + "".join(
            f"\n  facet {f['column']}: " + ", ".join(
                f"{v} {sum(1 for x in f['values'] if x == v)}" for v in f["levels"]
            )
            for f in cells.get("facets") or []
        )
    )


def facets_per_cell(cells: dict) -> list[str | None]:
    """One JSON object per cell, or None where no facets were asked for.

    Built here rather than in the reader so the cell row and its labels are
    written by the same statement, in the same order, from the same list.
    """
    columns = cells.get("facets") or []
    if not columns:
        return [None] * cells["n_cells"]
    return [
        json.dumps({f["column"]: f["values"][i] for f in columns})
        for i in range(cells["n_cells"])
    ]


def load(conn, name: str, species_id: int, cells: dict, source_checksum: str,
         units: str, annotation: str) -> tuple[int, int]:
    """Write the dataset, its catalogue and its cells in one transaction.

    Order matters twice over. `scrna_cells` references the catalogue with
    ON DELETE RESTRICT, so the cells go first or the catalogue delete is refused
    on every run after the first. And the dataset's counts and checksum are
    written last, after reading back what actually landed, so the row can never
    attest to a file it does not hold.

    A reload is refused outright when the dataset already has rows that name a
    cell type or a cell position, since nothing here can rebuild them.

    Returns the dataset id and the number of cells actually stored.
    """
    if len(cells["levels"]) > len(PALETTE):
        raise IngestError(
            f"{len(cells['levels'])} cell types and {len(PALETTE)} colours to "
            f"tell them apart; two would be drawn identically"
        )
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
            "(dataset_id, cell_number, barcode, x, y, cluster_id, replicate, "
            " facets) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (dataset_id, i, barcode, x, y, label, sample, facets)
                for i, (barcode, x, y, label, sample, facets) in enumerate(
                    zip(cells["barcodes"], cells["x"], cells["y"],
                        cells["labels"], cells["samples"],
                        facets_per_cell(cells))
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
    return dataset_id, stored


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        cells = read_cells(
            args.h5ad, args.annotation, args.sample_column,
            args.umap_key, args.expect_cells,
            tuple(args.group_column), tuple(args.facet),
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
            dataset_id, stored = load(
                conn, args.dataset_name, args.species_id, cells,
                checksum(args.h5ad), args.expression_units, args.annotation,
            )
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        # A species id that is right on one database and wrong on another is the
        # likeliest operator mistake, and it arrives as a foreign key violation.
        print(f"the database refused the load: {exc}", file=sys.stderr)
        return 1

    print(f"loaded {stored} cells into dataset {dataset_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
