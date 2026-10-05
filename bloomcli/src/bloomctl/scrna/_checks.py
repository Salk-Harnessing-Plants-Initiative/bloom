"""The refusals a dataset load makes before it writes, shared by `plan` and the load itself.

Each raises LoadError naming what is wrong; none writes.
"""

from __future__ import annotations

from collections import Counter

from ._cells import PALETTE
from ._writer import LoadError, Writer, dataset_name_ok, read_all

# Options that label the cells, recorded with the dataset when labels are added.
LABEL_KEYS = ("source_column", "genotype_column", "control", "constructs", "facets")

# The options a resumed load must share with the load it continues.
OPTION_KEYS = ("annotation", "sample_column", "umap_key", "expression_units", *LABEL_KEYS)

# Rows written after the cells. On a dataset whose cells are unfinished they mean something
# went wrong.
LATER_TABLES = (
    ("scrna_cluster_stats", "per-cluster statistics"),
    ("scrna_cluster_neighbors", "neighbour rows"),
    ("scrna_counts", "per-gene expression rows"),
    ("scrna_de", "differential expression rows"),
)

ADMIN = "replacing a loaded dataset is an admin task"


def check_registration(name: str, species_id: int, create: bool) -> None:
    if not create:
        raise LoadError(
            f"no dataset named {name!r} for species {species_id}. Pass --create to register "
            "a new one; without it a mistyped name would load a second copy"
        )
    if not dataset_name_ok(name):
        raise LoadError(
            f"{name!r} cannot be part of a storage path; use letters, digits, spaces, '.', "
            "'_' and '-'"
        )


def check_columns(cells: dict) -> None:
    if len(cells["levels"]) > len(PALETTE):
        raise LoadError(
            f"{len(cells['levels'])} cell types and {len(PALETTE)} colours to tell them apart; "
            "two would be drawn identically"
        )
    n = cells["n_cells"]
    for key in ("x", "y", "labels", "samples", "barcodes", "genotypes", "facets"):
        if cells.get(key) is not None and len(cells[key]) != n:
            raise LoadError(f"{key} holds {len(cells[key])} values for {n} cells")


def check_resume(found: dict, source_checksum: str, options: dict) -> str:
    """'resumed' or 'already loaded', or refuse."""
    dataset_id, stored = found["id"], found.get("source_checksum")
    if not stored:
        raise LoadError(
            f"dataset {dataset_id} records no source file, so this load cannot tell whether "
            f"it is the same one; {ADMIN}"
        )
    if found.get("ingested_at"):
        if stored == source_checksum:
            return "already loaded"
        raise LoadError(
            f"dataset {dataset_id} was loaded from a file with checksum {stored}; this "
            f"file's is {source_checksum}. {ADMIN}"
        )
    if stored != source_checksum:
        raise LoadError(
            f"dataset {dataset_id} was started from a file with checksum {stored}; this "
            f"file's is {source_checksum}. Resume it with the same file"
        )
    started = (found.get("metadata") or {}).get("load_options") or {}
    changed = [f"{k} was {started.get(k)!r}, now {options.get(k)!r}"
               for k in OPTION_KEYS if started.get(k) != options.get(k)]
    if changed:
        raise LoadError(
            f"dataset {dataset_id} was started with other options: {'; '.join(changed)}. "
            "Resume it with the same options"
        )
    return "resumed"


def check_nothing_later(writer: Writer, dataset_id: int) -> None:
    found = [what for table, what in LATER_TABLES if writer.read(
        lambda c, table=table: c.table(table).select("dataset_id")
        .eq("dataset_id", dataset_id).limit(1).execute().data)]
    if found:
        raise LoadError(
            f"dataset {dataset_id} has unfinished cells but already has "
            f"{' and '.join(found)}; an admin has to look at it"
        )


def check_labelled_dataset(
    writer: Writer, found: dict | None, name: str, species_id: int, cells: dict,
    source_checksum: str,
) -> None:
    """Labels go only on a finished dataset loaded from this file, holding these cells."""
    if found is None:
        raise LoadError(f"no dataset named {name!r} for species {species_id}; load its "
                        "cells first")
    dataset_id = found["id"]
    if not found.get("ingested_at"):
        raise LoadError(f"dataset {dataset_id}'s cells are not finished; finish loading them "
                        "before adding labels")
    if found.get("source_checksum") != source_checksum:
        raise LoadError(
            f"dataset {dataset_id} was loaded from a file with checksum "
            f"{found.get('source_checksum')}; this file's is {source_checksum}. Labels are "
            "paired to cells by position, so they have to come from the same file"
        )
    check_same_cells(writer, dataset_id, cells)


def check_same_cells(writer: Writer, dataset_id: int, cells: dict) -> None:
    """The stored cells have to be the file's, in the file's order, and so do the cell types."""
    stored = [r["barcode"] for r in read_all(
        writer, "scrna_cells", "cell_number,barcode",
        filters=[("eq", "dataset_id", dataset_id)], order="cell_number")]
    if stored != cells["barcodes"]:
        where = next((i for i, (a, b) in enumerate(zip(stored, cells["barcodes"])) if a != b),
                     min(len(stored), len(cells["barcodes"])))
        raise LoadError(
            f"dataset {dataset_id} does not hold these cells in this order: they first "
            f"differ at cell {where} ({len(stored)} stored, {len(cells['barcodes'])} in the "
            "file)"
        )
    if stored_catalogue(writer, dataset_id) != wanted_catalogue(cells):
        raise LoadError(f"dataset {dataset_id}'s stored cell types differ from the file's")


def stored_catalogue(writer: Writer, dataset_id: int) -> dict[str, int]:
    return {r["cluster_id"]: r["ordinal"] for r in read_all(
        writer, "scrna_clusters", "cluster_id,ordinal",
        filters=[("eq", "dataset_id", dataset_id)])}


def wanted_catalogue(cells: dict) -> dict[str, int]:
    return {level: i for i, level in enumerate(cells["levels"])}


def check_numbers(dataset_id: int, numbers: list[int], n: int, *, complete: bool) -> None:
    """Every stored cell_number in 0 … n−1 and each once; with ``complete``, all of them."""
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
        raise LoadError(
            f"dataset {dataset_id} stores {'; '.join(problems)}. It is not finished; an admin "
            "has to look at it"
        )
