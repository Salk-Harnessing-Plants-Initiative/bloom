"""Writing a dataset's cells: register or resume it, write what is missing, then finish it.

`plan` makes the same decisions without writing, so the user is shown what will happen before
being asked. A load that stops resumes from what is stored; a finished dataset is never
replaced -- that is an admin task.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from ._cells import PALETTE, genotype_rows
from ._checks import (
    LABEL_KEYS,
    check_columns,
    check_labelled_dataset,
    check_nothing_later,
    check_numbers,
    check_registration,
    check_resume,
    stored_catalogue,
    wanted_catalogue,
)
from ._writer import LoadError, Writer, find_dataset, insert, read_all, update

# Cells per insert request; each has to finish well inside the gateway's 60 s.
CELL_BATCH = 5000

# Cells per label update; their numbers travel in the request's URL.
LABEL_BATCH = 500


@dataclass(frozen=True)
class Plan:
    """What a load will do: 'register', 'resume', 'already loaded' or 'add labels'."""

    outcome: str
    dataset_id: int | None


def plan(
    writer: Writer, name: str, species_id: int, cells: dict, source_checksum: str,
    options: dict, *, create: bool, add_labels: bool = False,
) -> Plan:
    """Decide what the load will do, refusing as the load would; writes nothing."""
    name = _checked_name(name)
    check_columns(cells)
    found = find_dataset(writer, species_id, name)
    if add_labels:
        check_labelled_dataset(writer, found, name, species_id, cells, source_checksum)
        if cells.get("genotypes") is not None:
            _plan_genotypes(writer, found["id"], _genotypes(cells, options))
        return Plan("add labels", found["id"])
    if found is None:
        check_registration(name, species_id, create)
        return Plan("register", None)
    outcome = check_resume(found, source_checksum, options)
    if outcome == "resumed":
        check_nothing_later(writer, found["id"])
        return Plan("resume", found["id"])
    return Plan(outcome, found["id"])


def load(
    writer: Writer, name: str, species_id: int, cells: dict, source_checksum: str,
    options: dict, *, create: bool = False,
) -> tuple[int, int, str]:
    """Register or resume the dataset, write what is missing, then finish it.

    Returns the dataset id, the number of cells stored, and "registered", "resumed" or
    "already loaded".
    """
    name = _checked_name(name)
    check_columns(cells)
    found = find_dataset(writer, species_id, name)
    if found is None:
        check_registration(name, species_id, create)
        (found,) = insert(writer, "register the dataset", "scrna_datasets", [{
            "name": name, "species_id": species_id, "source_checksum": source_checksum,
            "metadata": {"load_options": options}}], returning=True)
        outcome = "registered"
    else:
        outcome = check_resume(found, source_checksum, options)
        if outcome == "already loaded":
            return found["id"], found.get("n_cells") or 0, outcome
        check_nothing_later(writer, found["id"])
    dataset_id, n = found["id"], cells["n_cells"]

    _write_catalogue(writer, dataset_id, cells, resuming=outcome == "resumed")
    genotype_ids = None
    if cells.get("genotypes") is not None:
        genotype_ids = _write_genotypes(
            writer, dataset_id, *_plan_genotypes(writer, dataset_id, _genotypes(cells, options))
        )
    numbers = _cell_numbers(writer, dataset_id) if outcome == "resumed" else []
    check_numbers(dataset_id, numbers, n, complete=False)
    have = set(numbers)
    _insert_cells(writer, dataset_id, cells, [i for i in range(n) if i not in have], genotype_ids)
    check_numbers(dataset_id, _cell_numbers(writer, dataset_id), n, complete=True)

    update(writer, "finish the dataset", "scrna_datasets", {
        "n_cells": n, "n_genes": cells["n_genes"],
        "expression_units": options["expression_units"],
        "metadata": {**_metadata(writer, dataset_id), "cell_type_column": options["annotation"]},
        "ingested_at": datetime.now(UTC).isoformat(),
    }, eq={"id": dataset_id})
    return dataset_id, n, outcome


def add_labels(
    writer: Writer, name: str, species_id: int, cells: dict, source_checksum: str,
    options: dict,
) -> tuple[int, dict]:
    """Add genotypes, cell labels and cell-type sources to a dataset loaded from this file.

    Its cells stay where they are. Everything is checked before anything is written, and
    every write sets fixed values, so running it again changes nothing more.
    """
    name = _checked_name(name)
    check_columns(cells)
    found = find_dataset(writer, species_id, name)
    check_labelled_dataset(writer, found, name, species_id, cells, source_checksum)
    dataset_id = found["id"]
    genotype_plan = None
    if cells.get("genotypes") is not None:
        genotype_plan = _plan_genotypes(writer, dataset_id, _genotypes(cells, options))

    added = {"genotypes": 0, "cells": 0, "sources": 0}
    for level, source in sorted((cells.get("sources") or {}).items()):
        update(writer, f"record where {level}'s label came from", "scrna_clusters",
               {"source": source.strip() or None},
               eq={"dataset_id": dataset_id, "cluster_id": level})
        added["sources"] += 1

    genotype_ids = None
    if genotype_plan is not None:
        genotype_ids = _write_genotypes(writer, dataset_id, *genotype_plan)
        added["genotypes"] = len(genotype_ids)
    if genotype_ids is not None or cells.get("facets") is not None:
        groups: dict[str, list[int]] = defaultdict(list)
        for i in range(cells["n_cells"]):
            groups[json.dumps(_cell_labels(cells, genotype_ids, i), sort_keys=True)].append(i)
        for key, numbers in groups.items():
            for start in range(0, len(numbers), LABEL_BATCH):
                chunk = numbers[start:start + LABEL_BATCH]
                update(writer, f"label cells {chunk[0]}–{chunk[-1]}", "scrna_cells",
                       json.loads(key), eq={"dataset_id": dataset_id},
                       in_={"cell_number": chunk})
        added["cells"] = cells["n_cells"]

    metadata = _metadata(writer, dataset_id)
    load_options = {**(metadata.get("load_options") or {}),
                    **{k: options.get(k) for k in LABEL_KEYS}}
    update(writer, "record the label options", "scrna_datasets",
           {"metadata": {**metadata, "load_options": load_options}}, eq={"id": dataset_id})
    return dataset_id, added


def catalogue_rows(dataset_id: int, cells: dict) -> list[dict]:
    """One cell type per label, sorted: ordinal is the index, colour the palette."""
    sources = cells.get("sources") or {}
    return [{"dataset_id": dataset_id, "cluster_id": level, "ordinal": i,
             "name": level, "color": PALETTE[i],
             "source": (sources.get(level) or "").strip() or None}
            for i, level in enumerate(cells["levels"])]


def _checked_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise LoadError("the dataset name is blank")
    return name


def _write_catalogue(writer: Writer, dataset_id: int, cells: dict, resuming: bool) -> None:
    wanted = wanted_catalogue(cells)
    if resuming:
        stored = stored_catalogue(writer, dataset_id)
        if stored == wanted:
            return
        if stored:
            def show(m):
                return ", ".join(f"{k}={v}" for k, v in sorted(m.items(), key=lambda kv: kv[1]))
            raise LoadError(
                f"dataset {dataset_id} holds the cell types {show(stored)}; the file has "
                f"{show(wanted)}"
            )
    insert(writer, "write the cell-type catalogue", "scrna_clusters",
           catalogue_rows(dataset_id, cells))


def _genotypes(cells: dict, options: dict) -> list[dict]:
    return genotype_rows(cells["genotypes"], options.get("control"),
                         options.get("constructs") or {})


def _plan_genotypes(writer: Writer, dataset_id: int, rows: list[dict]) -> tuple[dict, list[dict]]:
    """The ids of the genotypes already stored, and the rows still to write.

    A stored genotype that disagrees about being the control or its construct is refused,
    naming it: which line is which is not something to overwrite quietly.
    """
    stored = {g["name"]: g for g in read_all(
        writer, "scrna_genotypes", "id,name,is_control,construct",
        filters=[("eq", "dataset_id", dataset_id)])}
    for row in rows:
        have = stored.get(row["name"])
        if have and (bool(have["is_control"]), have.get("construct")) != (
                row["is_control"], row["construct"]):
            raise LoadError(
                f"dataset {dataset_id} already records genotype {row['name']!r} as "
                f"control={have['is_control']}, construct={have.get('construct')!r}; this "
                f"load says control={row['is_control']}, construct={row['construct']!r}"
            )
    ids = {name: g["id"] for name, g in stored.items()}
    return ids, [r for r in rows if r["name"] not in stored]


def _write_genotypes(writer: Writer, dataset_id: int, ids: dict, missing: list[dict]) -> dict:
    if missing:
        written = insert(writer, f"record {len(missing)} genotypes", "scrna_genotypes",
                         [{"dataset_id": dataset_id, **r} for r in missing], returning=True)
        ids = {**ids, **{g["name"]: g["id"] for g in written}}
    return ids


def _cell_numbers(writer: Writer, dataset_id: int) -> list[int]:
    return [r["cell_number"] for r in read_all(
        writer, "scrna_cells", "cell_number", filters=[("eq", "dataset_id", dataset_id)])]


def _cell_labels(cells: dict, genotype_ids: dict | None, i: int) -> dict:
    """The genotype and labels written on cell i, when the load has them."""
    out = {}
    if genotype_ids is not None:
        out["genotype_id"] = genotype_ids[cells["genotypes"][i]]
    if cells.get("facets") is not None:
        out["facets"] = cells["facets"][i]
    return out


def _insert_cells(
    writer: Writer, dataset_id: int, cells: dict, missing: list[int],
    genotype_ids: dict | None = None,
) -> None:
    for start in range(0, len(missing), CELL_BATCH):
        chunk = missing[start:start + CELL_BATCH]
        rows = [{"dataset_id": dataset_id, "cell_number": i,
                 "barcode": cells["barcodes"][i], "x": cells["x"][i], "y": cells["y"][i],
                 "cluster_id": cells["labels"][i], "replicate": cells["samples"][i],
                 **_cell_labels(cells, genotype_ids, i)}
                for i in chunk]
        insert(writer, f"insert cells {chunk[0]}–{chunk[-1]}", "scrna_cells", rows)


def _metadata(writer: Writer, dataset_id: int) -> dict:
    (current,) = writer.read(lambda c: c.table("scrna_datasets").select("metadata")
                             .eq("id", dataset_id).execute().data)
    return current.get("metadata") or {}
