"""Writing a dataset's per-gene expression: a row per gene, and one object per gene holding
its value in every cell, recorded once the object is stored.

The object is written before its row, so a stopped load leaves at most an object nothing
points at, which the next run writes again. A gene already recorded is skipped.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Callable

from . import _genes
from ._text import listed, visible
from ._writer import LoadError, Writer, insert, read_all

BUCKET = "scrna"

# The shape the bloom-js CLI writes: the id keeps datasets that share a name apart, the name
# keeps a bucket listing readable. Readers follow the path each scrna_counts row records.
COUNTS_PATH = "counts/{dataset}_{dataset_id}_/{gene}.json"

# Genes registered per request.
GENE_BATCH = 5000

# Genes recorded per request; each object is uploaded on its own and a row follows it.
RECORD_BATCH = 500


def clean_dataset_name(name: str) -> str:
    """The dataset name as the bloom-js CLI puts it in a path."""
    name = re.sub(r"^\\+", "", name.strip())
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r'[<>:"|?*\\%]', "", name)
    return re.sub(r"\.json$", "", name)


def object_path(dataset_name: str, dataset_id: int, gene: str) -> str:
    return COUNTS_PATH.format(dataset=clean_dataset_name(dataset_name), dataset_id=dataset_id,
                              gene=gene)


def missing(writer: Writer, dataset_id: int, names: list[str]) -> int:
    """How many of the file's genes the dataset has no counts for yet, refusing a record
    that does not describe this file."""
    rows = _stored_genes(writer, dataset_id)
    check_genes(dataset_id, rows, names)
    ids = {r["gene_name"]: r["id"] for r in rows}
    return len(names) - len(_recorded(writer, dataset_id, ids))


def write(
    writer: Writer, dataset_id: int, dataset_name: str, path: Path, names: list[str],
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Register the genes, then write every gene's object and row not already recorded.

    Returns how many genes were written. Afterwards every gene in the file is recorded once.
    """
    ids = _register(writer, dataset_id, names)
    done = _recorded(writer, dataset_id, ids)
    todo = [i for i, gene in enumerate(names) if gene not in done]
    pending: list[dict] = []
    for count, (column, values) in enumerate(_genes.gene_values(path, names, only=todo), 1):
        gene = names[column]
        object_at = object_path(dataset_name, dataset_id, gene)
        _upload(writer, object_at, json.dumps(values).encode())
        pending.append({"dataset_id": dataset_id, "gene_id": ids[gene],
                        "counts_object_path": object_at})
        if len(pending) >= RECORD_BATCH:
            _record(writer, pending)
            pending = []
        if on_progress:
            on_progress(count, len(todo))
    if pending:
        _record(writer, pending)
    recorded = _recorded(writer, dataset_id, ids)
    if len(recorded) != len(names):
        raise LoadError(f"dataset {dataset_id} records counts for {len(recorded):,} of "
                        f"{len(names):,} genes; it is not finished")
    return len(todo)


def check_genes(dataset_id: int, rows: list[dict], names: list[str]) -> None:
    """Registered genes have to be this file's, each once, at its position in the file."""
    seen = Counter(r["gene_name"] for r in rows)
    twice = sorted(g for g, n in seen.items() if n > 1)
    if twice:
        raise LoadError(f"dataset {dataset_id} has {listed(twice[:5])} registered more than "
                        "once; an admin has to remove the extra rows")
    position = {g: i for i, g in enumerate(names)}
    foreign = sorted(g for g in seen if g not in position)
    if foreign:
        raise LoadError(f"dataset {dataset_id} has {len(foreign)} genes registered that this "
                        f"file does not hold, e.g. {listed(foreign[:3])}")
    for r in rows:
        if r["gene_number"] != position[r["gene_name"]]:
            raise LoadError(f"{visible(r['gene_name'])} is gene {r['gene_number']} in dataset "
                            f"{dataset_id} and {position[r['gene_name']]} in this file")


def _stored_genes(writer: Writer, dataset_id: int) -> list[dict]:
    return read_all(writer, "scrna_genes", "id,gene_number,gene_name",
                    filters=[("eq", "dataset_id", dataset_id)])


def _register(writer: Writer, dataset_id: int, names: list[str]) -> dict[str, int]:
    """Every gene's row, numbered by its position in the file; a re-run lands on the rows
    the first run made. Returns each gene's id."""
    rows = _stored_genes(writer, dataset_id)
    check_genes(dataset_id, rows, names)
    known = {r["gene_name"] for r in rows}
    new = [(i, g) for i, g in enumerate(names) if g not in known]
    for start in range(0, len(new), GENE_BATCH):
        chunk = new[start:start + GENE_BATCH]
        insert(writer, f"register genes {chunk[0][0]}–{chunk[-1][0]}", "scrna_genes",
               [{"dataset_id": dataset_id, "gene_number": i, "gene_name": g} for i, g in chunk])
    if new:
        rows = _stored_genes(writer, dataset_id)
        check_genes(dataset_id, rows, names)
    if len(rows) != len(names):
        raise LoadError(f"dataset {dataset_id} has {len(rows):,} genes registered but the "
                        f"file holds {len(names):,}")
    return {r["gene_name"]: r["id"] for r in rows}


def _recorded(writer: Writer, dataset_id: int, ids: dict[str, int]) -> set[str]:
    """Genes whose object is already recorded, refusing one recorded twice."""
    rows = read_all(writer, "scrna_counts", "id,gene_id",
                    filters=[("eq", "dataset_id", dataset_id)])
    name_of = {v: k for k, v in ids.items()}
    recorded = Counter(r["gene_id"] for r in rows)
    twice = sorted(name_of.get(g, str(g)) for g, n in recorded.items() if n > 1)
    if twice:
        raise LoadError(f"dataset {dataset_id} records {listed(twice[:5])} more than once; "
                        "an admin has to remove the extra rows")
    return {name_of[g] for g in recorded if g in name_of}


def _upload(writer: Writer, path: str, payload: bytes) -> None:
    """One gene's values, replacing what is there, so a re-run repairs a stopped upload. The
    storage handle is taken from the current client on each request."""
    writer.write(f"upload {path}", lambda client: client.storage.from_(BUCKET).upload(
        path=path, file=payload,
        file_options={"content-type": "application/json", "upsert": "true"}))


def _record(writer: Writer, rows: list[dict]) -> None:
    insert(writer, f"record {len(rows)} genes", "scrna_counts", rows)
