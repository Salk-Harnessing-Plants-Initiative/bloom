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
# ~18x prod's largest per-gene file (0.9 MB on 2026-11-17); larger ones are not read.
MAX_COUNTS_BYTES = 16 * 1024**2
# Longest cell key shown in a note; a damaged file's key can be any length.
MAX_SHOWN_KEY = 20


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


def _index(key: str, cell_count: int) -> int:
    """The cell number `key` names, or -1 when it isn't a cell number below `cell_count`."""
    digits = key.lstrip("0") or "0"
    if not (key.isascii() and key.isdigit()) or len(digits) > len(str(cell_count)):
        return -1
    return int(digits)


def _shown(key: str) -> str:
    return repr(key if len(key) <= MAX_SHOWN_KEY else key[:MAX_SHOWN_KEY] + "…")


def _dense(sparse: dict[str, Any], cell_count: int) -> list[float]:
    values = [0.0] * cell_count
    for key, value in sparse.items():
        index = _index(key, cell_count)
        if not 0 <= index < cell_count:
            # The file was written against a different set of cells.
            raise CountsProblem(
                f"Its stored file names cell {_shown(key)}, but the dataset holds "
                f"{cell_count} cells, so the file doesn't match this dataset."
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CountsProblem(f"Its stored file has a non-number for cell {key}.")
        try:
            number = float(value)
        except OverflowError:
            number = math.inf
        if not math.isfinite(number):
            raise CountsProblem(
                f"Its stored file has a non-finite value for cell {key}."
            )
        values[index] = number
    return values
