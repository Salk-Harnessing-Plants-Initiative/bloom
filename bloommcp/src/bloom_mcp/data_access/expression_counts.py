"""One gene's stored expression in a scRNA dataset: a value per cell, zeros filled in.

The file is the one its scrna_counts row names (never a path built from names: production
holds two path shapes). It names only the cells where the gene is expressed, keyed by cell
number, as one object {"248": 1.62} or, in older files, a list of them [{"248": 1.62}],
as the website reads it (web/components/expression-lib/scrna-client.ts fetchGeneCounts).
"""

from __future__ import annotations

import json
import math
from typing import Any

from .file_access import path_problem
from .file_ports import FileNotFound, FileReader, FileReadError

BUCKET = "scrna"
# Far above any per-gene file on prod (set from storage sizes); larger ones are not read.
MAX_COUNTS_BYTES = 64 * 1024**2


class CountsProblem(Exception):
    """Why a gene's values can't be given; the message is shown to Claude as the gene's note."""


def gene_values(files: FileReader, path: str, cell_count: int) -> list[float]:
    """The gene's value in every one of the dataset's `cell_count` cells, by cell number."""
    if problem := path_problem(path):
        raise CountsProblem(f"Its stored file path {problem}, so it was not read.")
    try:
        size = files.size(BUCKET, path)
        if size > MAX_COUNTS_BYTES:
            raise CountsProblem(
                f"Its stored file is {size / 1024**2:.0f} MB, over the "
                f"{MAX_COUNTS_BYTES // 1024**2} MB this tool reads."
            )
        data = files.download(BUCKET, path)
    except FileNotFound:
        raise CountsProblem("Its stored file is missing.") from None
    except FileReadError:
        raise CountsProblem("Its stored file could not be read; try again.") from None
    return _dense(_parse(data), cell_count)


def _parse(data: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(data)
    except (ValueError, RecursionError):
        raise CountsProblem("Its stored file is not valid JSON.") from None
    if isinstance(parsed, list) and all(isinstance(e, dict) for e in parsed):
        merged: dict[str, Any] = {}
        for entry in parsed:
            merged.update(entry)
        return merged
    if isinstance(parsed, dict):
        return parsed
    raise CountsProblem("Its stored file is not a map of cells to values.")


def _dense(sparse: dict[str, Any], cell_count: int) -> list[float]:
    values = [0.0] * cell_count
    for key, value in sparse.items():
        index = int(key) if key.isascii() and key.isdigit() else -1
        if not 0 <= index < cell_count:
            # The file was written against a different set of cells.
            raise CountsProblem(
                f"Its stored file names cell {key!r}, but the dataset holds "
                f"{cell_count} cells, so the file doesn't match this dataset."
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CountsProblem(f"Its stored file has a non-number for cell {key}.")
        if not math.isfinite(value):
            raise CountsProblem(
                f"Its stored file has a non-finite value for cell {key}."
            )
        values[index] = float(value)
    return values
