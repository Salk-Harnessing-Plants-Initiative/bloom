"""Writing a dataset's cells: register or resume it, write what is missing, then finish it.

`plan` makes the same decisions without writing, so the user is shown what will happen before
being asked. A load that stops resumes from what is stored; a finished dataset is never
replaced: loading the file again takes a new name.
"""

from __future__ import annotations

import json
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from . import _counts
from ._cells import MAX_FACETS, MAX_FACETS_JSON, PALETTE, genotype_rows
from ._checks import (
    LABEL_KEYS,
    check_catalogue,
    check_columns,
    check_labelled_dataset,
    check_nothing_later,
    check_nothing_to_add,
    check_numbers,
    check_registration,
    check_resume,
    check_same_cells,
)
from ._text import visible
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
    options: dict, *, create: bool, add_labels: bool = False, species: str | None = None,
    counts: tuple[Path, list[str]] | None = None,
) -> Plan:
    """Decide what the load will do, refusing everything the load would; writes nothing."""
    name = _checked_name(name)
    check_columns(cells)
    found = find_dataset(writer, species_id, name)
    if add_labels:
        check_labelled_dataset(writer, found, name, species_id, cells, source_checksum, species)
        if cells.get("genotypes") is not None:
            _plan_genotypes(writer, found["id"], _genotypes(cells, options))
        if cells.get("facets") is not None:
            _merged_facets(writer, found["id"], cells)
        return Plan("add labels", found["id"])
    if found is None:
        check_registration(name, species_id, create, species)
        return Plan("register", None)
    outcome = check_resume(found, source_checksum, options)
    if outcome == "already loaded":
        check_nothing_to_add(found, options)
        if counts is not None and _counts.missing(writer, found["id"], counts[1]):
            check_same_cells(writer, found["id"], cells)
            return Plan("add counts", found["id"])
        return Plan(outcome, found["id"])
    dataset_id = found["id"]
    check_nothing_later(writer, dataset_id)
    check_catalogue(writer, dataset_id, cells)
    if counts is not None:
        _counts.missing(writer, dataset_id, counts[1])
    if cells.get("genotypes") is not None:
        _plan_genotypes(writer, dataset_id, _genotypes(cells, options))
    check_numbers(dataset_id, _cell_numbers(writer, dataset_id), cells["n_cells"], complete=False)
    return Plan("resume", dataset_id)


def load(
    writer: Writer, name: str, species_id: int, cells: dict, source_checksum: str,
    options: dict, *, create: bool = False, species: str | None = None,
    normalization: dict | None = None, on_progress: Callable[[int, int], None] | None = None,
    counts: tuple[Path, list[str]] | None = None, track=None,
) -> tuple[int, int, str]:
    """Register or resume the dataset, write its cells and, given ``counts`` (the file and its
    gene names), every gene's counts; then finish it.

    The registration records how many cells the file holds, so a reader can say how far an
    unfinished load got. The dataset is finished last, so finished means cells and counts are
    all there; ``normalization`` is recorded then. A dataset finished before counts were part
    of the load gets the ones it is missing. ``track(description, unit)`` shows each step's
    progress. Returns the dataset id, the number of cells stored, and "registered", "resumed",
    "counts added" or "already loaded".
    """
    name = _checked_name(name)
    check_columns(cells)
    found = find_dataset(writer, species_id, name)
    if found is None:
        check_registration(name, species_id, create, species)
        (found,) = insert(writer, "register the dataset", "scrna_datasets", [{
            "name": name, "species_id": species_id, "source_checksum": source_checksum,
            "metadata": {"load_options": options, "expected_cells": cells["n_cells"]}}],
            returning=True)
        outcome = "registered"
    else:
        outcome = check_resume(found, source_checksum, options)
        if outcome == "already loaded":
            check_nothing_to_add(found, options)
            if counts is not None and _counts.missing(writer, found["id"], counts[1]):
                check_same_cells(writer, found["id"], cells)
                _write_counts(writer, found["id"], name, counts, track)
                outcome = "counts added"
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
    with _step(track, "Writing cells") as report:
        _insert_cells(writer, dataset_id, cells, [i for i in range(n) if i not in have],
                      genotype_ids, report or on_progress)
    check_numbers(dataset_id, _cell_numbers(writer, dataset_id), n, complete=True)
    if counts is not None:
        _write_counts(writer, dataset_id, name, counts, track)

    metadata = {**_metadata(writer, dataset_id), "cell_type_column": options["annotation"]}
    if normalization is not None:
        metadata["normalization"] = normalization
    update(writer, "finish the dataset", "scrna_datasets", {
        "n_cells": n, "n_genes": cells["n_genes"],
        "expression_units": options["expression_units"],
        "metadata": metadata,
        "ingested_at": datetime.now(UTC).isoformat(),
    }, eq={"id": dataset_id})
    return dataset_id, n, outcome


def add_labels(
    writer: Writer, name: str, species_id: int, cells: dict, source_checksum: str,
    options: dict, *, species: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[int, dict]:
    """Add genotypes, cell labels and cell-type sources to a dataset loaded from this file.

    Its cells stay where they are, and labels given before are kept: a new --facet joins
    them, one of the same column replaces its values. Everything is checked before anything
    is written, and every write sets fixed values, so running it again changes nothing more.
    """
    name = _checked_name(name)
    check_columns(cells)
    found = find_dataset(writer, species_id, name)
    check_labelled_dataset(writer, found, name, species_id, cells, source_checksum, species)
    dataset_id = found["id"]
    genotype_plan = None
    if cells.get("genotypes") is not None:
        genotype_plan = _plan_genotypes(writer, dataset_id, _genotypes(cells, options))
    facets = _merged_facets(writer, dataset_id, cells) if cells.get("facets") is not None else None

    added = {"genotypes": 0, "cells": 0, "sources": 0}
    for level, source in sorted((cells.get("sources") or {}).items()):
        update(writer, f"record where {visible(level)}'s label came from", "scrna_clusters",
               {"source": source.strip() or None},
               eq={"dataset_id": dataset_id, "cluster_id": level})
        added["sources"] += 1

    genotype_ids = None
    if genotype_plan is not None:
        genotype_ids = _write_genotypes(writer, dataset_id, *genotype_plan)
        added["genotypes"] = len(genotype_ids)
    if genotype_ids is not None or facets is not None:
        labelled = {**cells, "facets": facets}
        groups: dict[str, list[int]] = defaultdict(list)
        for i in range(cells["n_cells"]):
            groups[json.dumps(_cell_labels(labelled, genotype_ids, i), sort_keys=True)].append(i)
        labelled_so_far = 0
        for key, numbers in groups.items():
            for start in range(0, len(numbers), LABEL_BATCH):
                chunk = numbers[start:start + LABEL_BATCH]
                update(writer, f"label cells {chunk[0]}–{chunk[-1]}", "scrna_cells",
                       json.loads(key), eq={"dataset_id": dataset_id},
                       in_={"cell_number": chunk})
                labelled_so_far += len(chunk)
                if on_progress:
                    on_progress(labelled_so_far, cells["n_cells"])
        added["cells"] = cells["n_cells"]

    metadata = _metadata(writer, dataset_id)
    load_options = _merged_options(metadata.get("load_options") or {}, options)
    update(writer, "record the label options", "scrna_datasets",
           {"metadata": {**metadata, "load_options": load_options}}, eq={"id": dataset_id})
    return dataset_id, added


def _merged_options(recorded: dict, options: dict) -> dict:
    """The recorded options with this run's labels added; options not given stay as they were."""
    merged = dict(recorded)
    if options.get("genotype_column"):
        for key in ("genotype_column", "control", "constructs"):
            merged[key] = options.get(key)
    if options.get("source_column"):
        merged["source_column"] = options["source_column"]
    if options.get("facets"):
        before = merged.get("facets") or []
        merged["facets"] = [*before, *(f for f in options["facets"] if f not in before)]
    return {k: merged.get(k) for k in dict.fromkeys([*recorded, *LABEL_KEYS])}


def _merged_facets(writer: Writer, dataset_id: int, cells: dict) -> list[dict]:
    """Each cell's stored labels with this file's added, refused if they no longer fit."""
    stored = read_all(writer, "scrna_cells", "cell_number,facets",
                      filters=[("eq", "dataset_id", dataset_id)], order="cell_number")
    merged = [{**(row.get("facets") or {}), **new} for row, new in zip(stored, cells["facets"])]
    if merged and len(merged[0]) > MAX_FACETS:
        raise LoadError(f"cells would carry {len(merged[0])} label columns; at most {MAX_FACETS}")
    for i, labels in enumerate(merged):
        if len(json.dumps(labels)) > MAX_FACETS_JSON:
            raise LoadError(f"cell {i}'s labels would come to more than {MAX_FACETS_JSON} "
                            "characters with the ones it already has; add fewer columns")
    return merged


def _write_counts(writer: Writer, dataset_id: int, name: str, counts, track) -> None:
    path, names = counts
    with _step(track, "Writing genes") as report:
        _counts.write(writer, dataset_id, name, path, names, on_progress=report)


@contextmanager
def _step(track, description: str):
    """One step's progress, when the caller shows progress."""
    if track is None:
        yield None
        return
    with track(description, "count") as report:
        yield report


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
    if resuming and check_catalogue(writer, dataset_id, cells):
        return
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
                f"dataset {dataset_id} already records genotype {visible(row['name'])!r} as "
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
    genotype_ids: dict | None = None, on_progress: Callable[[int, int], None] | None = None,
) -> None:
    for start in range(0, len(missing), CELL_BATCH):
        chunk = missing[start:start + CELL_BATCH]
        rows = [{"dataset_id": dataset_id, "cell_number": i,
                 "barcode": cells["barcodes"][i], "x": cells["x"][i], "y": cells["y"][i],
                 "cluster_id": cells["labels"][i], "replicate": cells["samples"][i],
                 **_cell_labels(cells, genotype_ids, i)}
                for i in chunk]
        insert(writer, f"insert cells {chunk[0]}–{chunk[-1]}", "scrna_cells", rows)
        if on_progress:
            on_progress(start + len(chunk), len(missing))


def _metadata(writer: Writer, dataset_id: int) -> dict:
    (current,) = writer.read(lambda c: c.table("scrna_datasets").select("metadata")
                             .eq("id", dataset_id).execute().data)
    return current.get("metadata") or {}
