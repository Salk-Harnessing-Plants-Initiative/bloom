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
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter
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

# The options a resumed load must share with the load it continues.
OPTION_KEYS = ("annotation", "sample_column", "umap_key", "source_column",
               "expression_units")

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
    sources = _text_column(adata, source_column) if source_column else None
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
        + (f"\n  label sources: "
           + ", ".join(sorted(set(cells["sources"].values())))
           if cells.get("sources") else "")
    )


def _check_columns(cells: dict) -> None:
    n = cells["n_cells"]
    for key in ("x", "y", "labels", "samples", "barcodes"):
        if len(cells[key]) != n:
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


def _insert_cells(writer, dataset_id: int, cells: dict, missing: list[int]) -> None:
    for start in range(0, len(missing), CELL_BATCH):
        chunk = missing[start:start + CELL_BATCH]
        rows = [{"dataset_id": dataset_id, "cell_number": i,
                 "barcode": cells["barcodes"][i], "x": cells["x"][i], "y": cells["y"][i],
                 "cluster_id": cells["labels"][i], "replicate": cells["samples"][i]}
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
    numbers = _cell_numbers(writer, dataset_id) if outcome == "resumed" else []
    _check_numbers(dataset_id, numbers, n, complete=False)
    have = set(numbers)
    _insert_cells(writer, dataset_id, cells, [i for i in range(n) if i not in have])
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        cells = read_cells(
            args.h5ad, args.annotation, args.sample_column,
            args.umap_key, args.expect_cells, args.source_column,
        )
    except IngestError as exc:
        print(f"refusing to ingest: {exc}", file=sys.stderr)
        return 1

    print(f"{args.dataset_name.strip()!r} from {args.h5ad.name}: {summarise(cells)}")

    if args.dry_run:
        print("dry run — nothing written")
        return 0

    if not args.email:
        print("refusing to ingest: --email names the account to sign in as; its "
              "password is read from BLOOM_PASSWORD", file=sys.stderr)
        return 1

    options = {"annotation": args.annotation, "sample_column": args.sample_column,
               "umap_key": args.umap_key, "source_column": args.source_column,
               "expression_units": args.expression_units}
    marker = ingest_api.Marker(ingest_api.marker_path(args.h5ad, args.dataset_name))
    try:
        marker.check()
        password = ingest_api.read_password()
        api_url, anon_key = ingest_api.resolve_api(args.server, args.api_url, args.anon_key)
        session = ingest_api.sign_in(api_url, anon_key, args.email, password)
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
