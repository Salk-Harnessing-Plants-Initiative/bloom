"""The checks every scRNA tool makes first: a live, non-reference dataset and its cells."""

from __future__ import annotations

from typing import Any

from bloom_mcp.data_access import scrna_de_reads as de
from bloom_mcp.data_access.gene_reads import Reads
from bloom_mcp.tools import _ports

from ._common import fix_request

# Most cells read per dataset, ~6x prod's biggest (32,522 on 2026-11-17); past it, the link only.
MAX_CELLS = 200_000
# A dataset kind with no cells of its own (the website treats it as not found).
REFERENCE_KIND = "reference"
CELL_ARRAYS = "scrna_cell_arrays"


def dataset(reads: Reads, dataset_id: int, lacks: str = "cells") -> dict[str, Any]:
    """The live dataset, refusing a missing, deleted or reference one (it has no `lacks`)."""
    found = de.live_dataset(reads, dataset_id)
    if found is None:
        raise fix_request(
            f"No scRNA dataset {dataset_id}.",
            "Use a dataset id from the gene lookup or scrna_datasets.",
        )
    if found.get("kind") == REFERENCE_KIND:
        raise fix_request(
            f"Dataset {dataset_id} is a reference atlas with no {lacks} of its own.",
            "Use one of the datasets the gene lookup lists for the gene.",
        )
    return found


def cells(dataset_id: int) -> list[dict[str, Any]]:
    """The dataset's cells in cell order, refusing more than MAX_CELLS."""
    found = _ports.table_reader().call(
        CELL_ARRAYS, {"ds_id": dataset_id}, [], MAX_CELLS + 1, 0
    )
    if len(found) > MAX_CELLS:
        raise fix_request(
            f"Dataset {dataset_id} has over {MAX_CELLS:,} cells, more than this tool reads.",
            "Give the user the dataset's page link to see the genes there.",
        )
    return found
