#!/usr/bin/env python3
"""
Load a single-cell dataset's cells into the expression explorer.

Reads an `.h5ad` and writes three tables: `scrna_datasets` (the registration),
`scrna_clusters` (the cell-type catalogue, one row per label with an ordinal and
a colour) and `scrna_cells` (one row per cell: its UMAP coordinates, its cell
type, and which sample it came from). Per-gene expression is loaded afterwards
by ingest_scrnaseq_counts.py.

It signs in to the site as a writer (or admin) account and writes through the
API. The password is read from BLOOM_PASSWORD, never from the command line:

    BLOOM_PASSWORD=... uv run --with anndata --with supabase \
      python scripts/ingest_scrnaseq.py \
        --server https://staging.bloom.salk.edu --email you@salk.edu \
        --h5ad myb41_joint_SATURN_LABELS.h5ad \
        --dataset-name "MYB41 transgene" --species-id 1 \
        --annotation nn_label_plain --expect-cells 8683 --create

--server reads the API address from the site; --api-url and --anon-key give it
directly instead. An admin makes an account a writer with:

    UPDATE auth.users SET raw_app_meta_data = raw_app_meta_data || '{"is_writer": true}'
    WHERE email = 'you@salk.edu';

--create registers a new dataset; without it an unknown name is refused, so a
typo cannot load a second copy. If a load stops, run the same command again: it
continues from what is already stored. A finished dataset is not loaded again;
replacing one is an admin task. Load a dataset from one terminal at a time.

Genotypes and labels the map can filter on come from obs columns:

    --genotype-column sample --control Col-0 --construct pFACT=pFACT:MYB41 \
    --facet transgene_pos --facet saturn_timezone --source-column nn_source

With --add-labels, the same options add these to a dataset already loaded from
this file, leaving its cells where they are.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrna_ingest_api as ingest_api

IngestError = ingest_api.IngestError

# Cluster colours, one per ordinal, so a cell type is the same colour for every
# user. This supersedes the 20-colour list in
# scripts/backfill_scrna_cluster_colors.sql, which cannot cover the 23 cell types
# this dataset carries.
PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
    "#86BCB6", "#F1CE63", "#D37295", "#A0CBE8", "#FFBE7D",
    "#8CD17D", "#B6992D", "#499894", "#FABFD2", "#D4A6C8",
    "#79706E", "#D7B5A6", "#6B4C9A",
]

# Cells per insert request; each has to finish well inside the gateway's 60 s.
CELL_BATCH = 5000

# Options that label the cells, recorded with the dataset when labels are added.
LABEL_KEYS = ("source_column", "genotype_column", "control", "constructs", "facets")

# The options a resumed load must share with the load it continues.
OPTION_KEYS = ("annotation", "sample_column", "umap_key", "expression_units",
               *LABEL_KEYS)

# A label becomes a row of toggles, so it has to be a handful of values. More is a
# measurement, and would reach the browser as hundreds of buttons.
MAX_FACET_VALUES = 12

# The database's limits on a cell's labels (scrna_facets_are_flat_text) and on a
# genotype (scrna_genotypes_lengths).
MAX_FACETS = 32
MAX_FACET_KEY = 64
MAX_FACET_VALUE = 200
MAX_FACETS_JSON = 1024
MAX_GENOTYPE_NAME = 100
MAX_CONSTRUCT = 200

# Cells per label update; their numbers travel in the request's URL.
LABEL_BATCH = 500

# Rows written after the cells. On a dataset whose cells are unfinished they
# mean something went wrong.
LATER_TABLES = (
    ("scrna_cluster_stats", "per-cluster statistics"),
    ("scrna_cluster_neighbors", "neighbour rows"),
    ("scrna_counts", "per-gene expression rows"),
    ("scrna_de", "differential expression rows"),
)

ADMIN = "replacing a loaded dataset is an admin task"

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
    p.add_argument(
        "--source-column",
        help="an obs column naming where each cell's label came from, such as "
             "the reference atlas it was transferred from. Recorded per cell "
             "type, since that is where it varies",
    )
    p.add_argument(
        "--genotype-column",
        help="an obs column naming each cell's genotype. Each value becomes a "
             "genotype the cells point at; needs --control",
    )
    p.add_argument("--control", metavar="GENOTYPE",
                   help="which genotype is the control, named rather than guessed")
    p.add_argument("--construct", action="append", default=[], metavar="GENOTYPE=NAME",
                   help="the construct a transgenic line carries; repeatable")
    p.add_argument("--facet", action="append", default=[], metavar="COLUMN",
                   help="an obs column of labels to filter the map by, such as "
                        "transgene status. A few short values only; repeatable")
    p.add_argument("--add-labels", action="store_true",
                   help="add the genotypes, labels and cell-type sources to a dataset "
                        "already loaded from this file, leaving its cells as they are")
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
             "cannot load a second copy alongside the real one. Accepted when "
             "resuming, so the same command continues a stopped load",
    )
    p.add_argument("--server", help="the site, e.g. https://staging.bloom.salk.edu; "
                   "the API address is read from it")
    p.add_argument("--api-url", help="the API address, instead of reading it from --server")
    p.add_argument("--anon-key", help="the site's public key, with --api-url")
    p.add_argument("--email", help="the writer account to sign in as; the password "
                   "is read from BLOOM_PASSWORD")
    p.add_argument("--dry-run", action="store_true",
                   help="read and check the file, write nothing")
    return p.parse_args(argv)


def read_cells(
    h5ad_path: Path,
    annotation: str,
    sample_column: str,
    umap_key: str,
    expect_cells: int | None,
    source_column: str | None = None,
    genotype_column: str | None = None,
    facet_columns: tuple[str, ...] = (),
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

    for column in (annotation, sample_column, source_column, genotype_column):
        if column and column not in adata.obs:
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
    sources = _text_column(adata, source_column) if source_column else None
    genotypes = _text_column(adata, genotype_column) if genotype_column else None
    too_long = sorted({g for g in genotypes or () if len(g) > MAX_GENOTYPE_NAME})
    if too_long:
        raise IngestError(f"genotype names longer than {MAX_GENOTYPE_NAME} characters: "
                          f"{', '.join(too_long[:3])}")
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
        "sources": label_sources(labels, sources) if sources else {},
        "genotypes": genotypes,
        "facets": read_facets(adata, facet_columns) if facet_columns else None,
    }


def read_facets(adata, columns: tuple[str, ...]) -> list[dict[str, str]]:
    """Each cell's labels to filter the map by, as {column: value}.

    Refused rather than truncated when a column has too many values: a label is a
    row of toggles, and a column with hundreds of them is a measurement.
    """
    if len(columns) > MAX_FACETS:
        raise IngestError(f"{len(columns)} label columns; a cell holds at most {MAX_FACETS}")
    per_column = {}
    for column in columns:
        if column not in adata.obs:
            raise IngestError(f"no obs[{column!r}] to use as a label. Found: "
                              f"{', '.join(sorted(adata.obs.columns))}")
        if len(column) > MAX_FACET_KEY:
            raise IngestError(f"label column {column!r} is longer than {MAX_FACET_KEY} characters")
        values = _text_column(adata, column)
        levels = sorted(set(values))
        if len(levels) > MAX_FACET_VALUES:
            raise IngestError(f"obs[{column!r}] has {len(levels)} values, more than the "
                              f"{MAX_FACET_VALUES} a row of toggles can show; it looks like "
                              f"a measurement rather than a label")
        long = [v for v in levels if len(v) > MAX_FACET_VALUE]
        if long:
            raise IngestError(f"obs[{column!r}] has values longer than {MAX_FACET_VALUE} "
                              f"characters, e.g. {long[0][:40]!r}")
        per_column[column] = values
    facets = [{c: per_column[c][i] for c in columns} for i in range(adata.n_obs)]
    for i, cell in enumerate(facets):
        if len(json.dumps(cell)) > MAX_FACETS_JSON:
            raise IngestError(f"cell {i}'s labels come to more than {MAX_FACETS_JSON} "
                              f"characters; label fewer columns")
    return facets


def parse_constructs(pairs: list[str]) -> dict[str, str]:
    """`GENOTYPE=NAME` arguments, refused rather than ignored when malformed."""
    out = {}
    for pair in pairs:
        genotype, sep, construct = pair.partition("=")
        if not sep or not genotype.strip() or not construct.strip():
            raise IngestError(f"--construct wants GENOTYPE=NAME, got {pair!r}")
        if len(construct.strip()) > MAX_CONSTRUCT:
            raise IngestError(f"the construct for {genotype.strip()!r} is longer than "
                              f"{MAX_CONSTRUCT} characters")
        out[genotype.strip()] = construct.strip()
    return out


def genotype_rows(genotypes: list[str], control: str | None,
                  constructs: dict[str, str]) -> list[dict]:
    """One row per genotype in the file: which is the control, and each line's construct."""
    names = sorted(set(genotypes))
    if control is None:
        raise IngestError("--genotype-column needs --control, naming which genotype is "
                          "the control; it is not guessed")
    if control not in names:
        raise IngestError(f"--control names {control!r}, which is not a genotype in this "
                          f"file: {', '.join(names)}")
    unknown = sorted(set(constructs) - set(names))
    if unknown:
        raise IngestError(f"--construct names {', '.join(unknown)}, not a genotype in this "
                          f"file: {', '.join(names)}")
    return [{"name": n, "is_control": n == control, "construct": constructs.get(n)}
            for n in names]


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


def label_sources(labels: list[str], sources: list[str]) -> dict[str, str]:
    """Summarise, per cell type, where its label came from.
    """
    per_type: dict[str, Counter] = {}
    for label, source in zip(labels, sources):
        per_type.setdefault(label, Counter())[source] += 1

    summary = {}
    for label, counts in per_type.items():
        if len(counts) == 1:
            summary[label] = next(iter(counts))
        else:
            total = sum(counts.values())
            summary[label] = ", ".join(
                f"{name} ({round(n * 100 / total)}%)"
                for name, n in counts.most_common()
            )
    return summary


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
        + ("\n  label sources: "
           + ", ".join(sorted(set(cells["sources"].values())))
           if cells.get("sources") else "")
        + "".join(
            f"\n  label {column}: " + ", ".join(
                f"{value} {n}" for value, n in sorted(
                    Counter(f[column] for f in cells["facets"]).items()))
            for column in (cells["facets"][0] if cells.get("facets") else ())
        )
    )


def _check_columns(cells: dict) -> None:
    n = cells["n_cells"]
    for key in ("x", "y", "labels", "samples", "barcodes", "genotypes", "facets"):
        if cells.get(key) is not None and len(cells[key]) != n:
            raise IngestError(f"{key} holds {len(cells[key])} values for {n} cells")


def _check_resume(found: dict, source_checksum: str, options: dict) -> str:
    """'resumed' or 'already loaded', or refuse."""
    dataset_id, stored = found["id"], found.get("source_checksum")
    if not stored:
        raise IngestError(f"dataset {dataset_id} records no source file, so this "
                          f"load cannot tell whether it is the same one; {ADMIN}")
    if found.get("ingested_at"):
        if stored == source_checksum:
            return "already loaded"
        raise IngestError(f"dataset {dataset_id} was loaded from a file with checksum "
                          f"{stored}; this file's is {source_checksum}. {ADMIN}")
    if stored != source_checksum:
        raise IngestError(f"dataset {dataset_id} was started from a file with checksum "
                          f"{stored}; this file's is {source_checksum}. Resume it with "
                          f"the same file")
    started = (found.get("metadata") or {}).get("load_options") or {}
    changed = [f"{k} was {started.get(k)!r}, now {options.get(k)!r}"
               for k in OPTION_KEYS if started.get(k) != options.get(k)]
    if changed:
        raise IngestError(f"dataset {dataset_id} was started with other options: "
                          f"{'; '.join(changed)}. Resume it with the same options")
    return "resumed"


def _check_nothing_later(writer, dataset_id: int) -> None:
    found = [what for table, what in LATER_TABLES if writer.read(
        lambda c, table=table: c.table(table).select("dataset_id")
        .eq("dataset_id", dataset_id).limit(1).execute().data)]
    if found:
        raise IngestError(f"dataset {dataset_id} has unfinished cells but already has "
                          f"{' and '.join(found)}; an admin has to look at it")


def catalogue_rows(dataset_id: int, cells: dict) -> list[dict]:
    """One cell type per label, sorted: ordinal is the index, colour the palette."""
    sources = cells.get("sources") or {}
    return [{"dataset_id": dataset_id, "cluster_id": level, "ordinal": i,
             "name": level, "color": PALETTE[i],
             "source": (sources.get(level) or "").strip() or None}
            for i, level in enumerate(cells["levels"])]


def _write_catalogue(writer, dataset_id: int, cells: dict, resuming: bool) -> None:
    wanted = {level: i for i, level in enumerate(cells["levels"])}
    if resuming:
        stored = {r["cluster_id"]: r["ordinal"] for r in ingest_api.read_all(
            writer, "scrna_clusters", "cluster_id,ordinal",
            filters=[("eq", "dataset_id", dataset_id)])}
        if stored == wanted:
            return
        if stored:
            def show(m):
                return ", ".join(f"{k}={v}" for k, v in sorted(m.items(), key=lambda kv: kv[1]))
            raise IngestError(f"dataset {dataset_id} holds the cell types {show(stored)}; "
                              f"the file has {show(wanted)}")
    ingest_api.insert(writer, "write the cell-type catalogue", "scrna_clusters",
                      catalogue_rows(dataset_id, cells))


def _plan_genotypes(writer, dataset_id: int, rows: list[dict]) -> tuple[dict, list[dict]]:
    """The ids of the genotypes already stored, and the rows still to write.

    A stored genotype that disagrees about being the control or its construct is
    refused, naming it: which line is which is not something to overwrite quietly.
    """
    stored = {g["name"]: g for g in ingest_api.read_all(
        writer, "scrna_genotypes", "id,name,is_control,construct",
        filters=[("eq", "dataset_id", dataset_id)])}
    for row in rows:
        have = stored.get(row["name"])
        if have and (bool(have["is_control"]), have.get("construct")) != (
                row["is_control"], row["construct"]):
            raise IngestError(
                f"dataset {dataset_id} already records genotype {row['name']!r} as "
                f"control={have['is_control']}, construct={have.get('construct')!r}; "
                f"this load says control={row['is_control']}, construct={row['construct']!r}")
    ids = {name: g["id"] for name, g in stored.items()}
    return ids, [r for r in rows if r["name"] not in stored]


def _write_genotypes(writer, dataset_id: int, ids: dict, missing: list[dict]) -> dict:
    if missing:
        written = ingest_api.insert(writer, f"record {len(missing)} genotypes",
                                    "scrna_genotypes",
                                    [{"dataset_id": dataset_id, **r} for r in missing],
                                    returning=True)
        ids = {**ids, **{g["name"]: g["id"] for g in written}}
    return ids


def _cell_numbers(writer, dataset_id: int) -> list[int]:
    return [r["cell_number"] for r in ingest_api.read_all(
        writer, "scrna_cells", "cell_number", filters=[("eq", "dataset_id", dataset_id)])]


def _check_numbers(dataset_id: int, numbers: list[int], n: int, *, complete: bool) -> None:
    """Every stored cell_number in 0 … n−1 and each once; with `complete`, all of them."""
    seen = Counter(numbers)
    problems = []
    repeated = sorted(k for k, v in seen.items() if v > 1)
    outside = sorted(k for k in seen if not 0 <= k < n)
    if repeated:
        problems.append(f"{len(repeated)} cell numbers repeated (e.g. {repeated[:3]})")
    if outside:
        problems.append(f"{len(outside)} outside 0–{n - 1} (e.g. {outside[:3]})")
    if complete and len(seen) - len(outside) != n:
        problems.append(f"{n - (len(seen) - len(outside))} of {n} cells missing")
    if problems:
        raise IngestError(f"dataset {dataset_id} stores {'; '.join(problems)}. It is "
                          f"not finished; an admin has to look at it")


def _cell_labels(cells: dict, genotype_ids: dict | None, i: int) -> dict:
    """The genotype and labels written on cell i, when the load has them."""
    out = {}
    if genotype_ids is not None:
        out["genotype_id"] = genotype_ids[cells["genotypes"][i]]
    if cells.get("facets") is not None:
        out["facets"] = cells["facets"][i]
    return out


def _insert_cells(writer, dataset_id: int, cells: dict, missing: list[int],
                  genotype_ids: dict | None = None) -> None:
    for start in range(0, len(missing), CELL_BATCH):
        chunk = missing[start:start + CELL_BATCH]
        rows = [{"dataset_id": dataset_id, "cell_number": i,
                 "barcode": cells["barcodes"][i], "x": cells["x"][i], "y": cells["y"][i],
                 "cluster_id": cells["labels"][i], "replicate": cells["samples"][i],
                 **_cell_labels(cells, genotype_ids, i)}
                for i in chunk]
        ingest_api.insert(writer, f"insert cells {chunk[0]}–{chunk[-1]}",
                          "scrna_cells", rows)


def load(writer, name: str, species_id: int, cells: dict, source_checksum: str,
         options: dict, *, create: bool = False) -> tuple[int, int, str]:
    """Register or resume the dataset, write what is missing, then finish it.

    Returns the dataset id, the number of cells stored, and "registered",
    "resumed" or "already loaded".
    """
    name = name.strip()
    if not name:
        raise IngestError("the dataset name is blank")
    if len(cells["levels"]) > len(PALETTE):
        raise IngestError(f"{len(cells['levels'])} cell types and {len(PALETTE)} "
                          f"colours to tell them apart; two would be drawn identically")
    _check_columns(cells)

    found = ingest_api.find_dataset(writer, species_id, name)
    if found is None:
        if not create:
            raise IngestError(f"no dataset named {name!r} for species {species_id}. "
                              f"Pass --create to register a new one; without it a "
                              f"mistyped name would load a second copy")
        if not ingest_api.dataset_name_ok(name):
            raise IngestError(f"{name!r} cannot be part of a storage path; use letters, "
                              f"digits, spaces, '.', '_' and '-'")
        (found,) = ingest_api.insert(writer, "register the dataset", "scrna_datasets", [{
            "name": name, "species_id": species_id, "source_checksum": source_checksum,
            "metadata": {"load_options": options}}], returning=True)
        outcome = "registered"
    else:
        outcome = _check_resume(found, source_checksum, options)
        if outcome == "already loaded":
            return found["id"], found.get("n_cells") or 0, outcome
        _check_nothing_later(writer, found["id"])
    dataset_id, n = found["id"], cells["n_cells"]

    _write_catalogue(writer, dataset_id, cells, resuming=outcome == "resumed")
    genotype_ids = None
    if cells.get("genotypes") is not None:
        rows = genotype_rows(cells["genotypes"], options.get("control"),
                             options.get("constructs") or {})
        genotype_ids = _write_genotypes(writer, dataset_id,
                                        *_plan_genotypes(writer, dataset_id, rows))
    numbers = _cell_numbers(writer, dataset_id) if outcome == "resumed" else []
    _check_numbers(dataset_id, numbers, n, complete=False)
    have = set(numbers)
    _insert_cells(writer, dataset_id, cells, [i for i in range(n) if i not in have],
                  genotype_ids)
    _check_numbers(dataset_id, _cell_numbers(writer, dataset_id), n, complete=True)

    (current,) = writer.read(lambda c: c.table("scrna_datasets").select("metadata")
                             .eq("id", dataset_id).execute().data)
    ingest_api.update(writer, "finish the dataset", "scrna_datasets", {
        "n_cells": n, "n_genes": cells["n_genes"],
        "expression_units": options["expression_units"],
        "metadata": {**(current.get("metadata") or {}),
                     "cell_type_column": options["annotation"]},
        "ingested_at": datetime.now(UTC).isoformat(),
    }, eq={"id": dataset_id})
    return dataset_id, n, outcome


def _check_same_cells(writer, dataset_id: int, cells: dict) -> None:
    """Labels are paired to cells by position, so the stored cells have to be the
    file's, in the file's order, and so do the stored cell types."""
    stored = [r["barcode"] for r in ingest_api.read_all(
        writer, "scrna_cells", "cell_number,barcode",
        filters=[("eq", "dataset_id", dataset_id)], order="cell_number")]
    if stored != cells["barcodes"]:
        where = next((i for i, (a, b) in enumerate(zip(stored, cells["barcodes"])) if a != b),
                     min(len(stored), len(cells["barcodes"])))
        raise IngestError(f"dataset {dataset_id} does not hold these cells in this order: "
                          f"they first differ at cell {where} ({len(stored)} stored, "
                          f"{len(cells['barcodes'])} in the file)")
    catalogue = {r["cluster_id"]: r["ordinal"] for r in ingest_api.read_all(
        writer, "scrna_clusters", "cluster_id,ordinal",
        filters=[("eq", "dataset_id", dataset_id)])}
    if catalogue != {level: i for i, level in enumerate(cells["levels"])}:
        raise IngestError(f"dataset {dataset_id}'s stored cell types differ from the file's")


def add_labels(writer, name: str, species_id: int, cells: dict, source_checksum: str,
               options: dict) -> tuple[int, dict]:
    """Add genotypes, cell labels and cell-type sources to a dataset already loaded
    from this file, leaving its cells where they are.

    Everything is checked before anything is written. Every write sets fixed
    values, so running it again changes nothing more. Returns the dataset id and
    how many genotypes, cells and cell-type sources were written.
    """
    name = name.strip()
    _check_columns(cells)
    found = ingest_api.find_dataset(writer, species_id, name)
    if found is None:
        raise IngestError(f"no dataset named {name!r} for species {species_id}; load its "
                          f"cells first")
    dataset_id = found["id"]
    if not found.get("ingested_at"):
        raise IngestError(f"dataset {dataset_id}'s cells are not finished; finish loading "
                          f"them before adding labels")
    if found.get("source_checksum") != source_checksum:
        raise IngestError(f"dataset {dataset_id} was loaded from a file with checksum "
                          f"{found.get('source_checksum')}; this file's is {source_checksum}. "
                          f"Labels are paired to cells by position, so they have to come "
                          f"from the same file")
    _check_same_cells(writer, dataset_id, cells)
    plan = None
    if cells.get("genotypes") is not None:
        rows = genotype_rows(cells["genotypes"], options.get("control"),
                             options.get("constructs") or {})
        plan = _plan_genotypes(writer, dataset_id, rows)

    added = {"genotypes": 0, "cells": 0, "sources": 0}
    for level, source in sorted((cells.get("sources") or {}).items()):
        ingest_api.update(writer, f"record where {level}'s label came from", "scrna_clusters",
                          {"source": source.strip() or None},
                          eq={"dataset_id": dataset_id, "cluster_id": level})
        added["sources"] += 1

    genotype_ids = None
    if plan is not None:
        genotype_ids = _write_genotypes(writer, dataset_id, *plan)
        added["genotypes"] = len(genotype_ids)
    if genotype_ids is not None or cells.get("facets") is not None:
        groups: dict[str, list[int]] = defaultdict(list)
        for i in range(cells["n_cells"]):
            groups[json.dumps(_cell_labels(cells, genotype_ids, i), sort_keys=True)].append(i)
        for key, numbers in groups.items():
            for start in range(0, len(numbers), LABEL_BATCH):
                chunk = numbers[start:start + LABEL_BATCH]
                ingest_api.update(writer, f"label cells {chunk[0]}–{chunk[-1]}", "scrna_cells",
                                  json.loads(key), eq={"dataset_id": dataset_id},
                                  in_={"cell_number": chunk})
        added["cells"] = cells["n_cells"]

    (current,) = writer.read(lambda c: c.table("scrna_datasets").select("metadata")
                             .eq("id", dataset_id).execute().data)
    metadata = current.get("metadata") or {}
    load_options = {**(metadata.get("load_options") or {}),
                    **{k: options.get(k) for k in LABEL_KEYS}}
    ingest_api.update(writer, "record the label options", "scrna_datasets",
                      {"metadata": {**metadata, "load_options": load_options}},
                      eq={"id": dataset_id})
    return dataset_id, added


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        if args.control and not args.genotype_column:
            raise IngestError("--control names a genotype, so it needs --genotype-column")
        constructs = parse_constructs(args.construct)
        cells = read_cells(
            args.h5ad, args.annotation, args.sample_column,
            args.umap_key, args.expect_cells, args.source_column,
            args.genotype_column, tuple(args.facet),
        )
        genotypes = (genotype_rows(cells["genotypes"], args.control, constructs)
                     if cells["genotypes"] is not None else [])
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1

    print(f"{args.dataset_name.strip()!r} from {args.h5ad.name}: {summarise(cells)}")
    if genotypes:
        print("  genotypes: " + ", ".join(
            f"{g['name']} (control)" if g["is_control"]
            else f"{g['name']} ({g['construct']})" if g["construct"] else g["name"]
            for g in genotypes))

    if args.dry_run:
        print("dry run — nothing written")
        return 0

    if not args.email:
        print("refusing to ingest: --email names the account to sign in as; its "
              "password is read from BLOOM_PASSWORD", file=sys.stderr)
        return 1

    options = {"annotation": args.annotation, "sample_column": args.sample_column,
               "umap_key": args.umap_key, "source_column": args.source_column,
               "expression_units": args.expression_units,
               "genotype_column": args.genotype_column, "control": args.control,
               "constructs": constructs or None, "facets": list(args.facet) or None}
    if args.add_labels and not (args.source_column or args.genotype_column or args.facet):
        print("refusing to ingest: --add-labels needs --source-column, --genotype-column "
              "or --facet to add", file=sys.stderr)
        return 1
    marker = ingest_api.Marker(ingest_api.marker_path(args.h5ad, args.dataset_name))
    try:
        marker.check()
        password = ingest_api.read_password()
        api_url, anon_key = ingest_api.resolve_api(args.server, args.api_url, args.anon_key)
        session = ingest_api.sign_in(api_url, anon_key, args.email, password)
        if args.add_labels:
            dataset_id, added = add_labels(
                ingest_api.Writer(session, marker), args.dataset_name, args.species_id,
                cells, checksum(args.h5ad), options,
            )
            print(f"added labels to dataset {dataset_id} ({args.dataset_name.strip()!r}): "
                  f"{added['genotypes']} genotypes, {added['cells']} cells labelled, "
                  f"{added['sources']} cell-type sources")
            return 0
        dataset_id, stored, outcome = load(
            ingest_api.Writer(session, marker), args.dataset_name, args.species_id,
            cells, checksum(args.h5ad), options, create=args.create,
        )
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1

    name = args.dataset_name.strip()
    if outcome == "already loaded":
        print(f"dataset {dataset_id} ({name!r}) is already loaded from this file: "
              f"{stored} cells")
    else:
        print(f"{outcome} dataset {dataset_id} ({name!r}): {stored} cells")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
