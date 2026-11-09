"""A fake table reader injected for the bloom_data tool tests."""

from __future__ import annotations

import copy
import random

import pytest

from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.tools import _ports

# Shuffled, so a test passes only if the tool sorts by the paging key itself.
SPECIES = [
    {"id": i, "common_name": f"plant {i}", "deleted_at": None} for i in range(1, 121)
]
random.Random(0).shuffle(SPECIES)
GENES = [{"gene_id": f"AT{i:05d}", "name": None} for i in range(1, 31)]
random.Random(1).shuffle(GENES)
CLUSTER_STATS = [
    {"dataset_id": d, "cluster_id": c, "n_cells": 10} for d in (2, 1) for c in (3, 1, 2)
]
SCANS_EXTENDED = [
    {"scan_id": i, "experiment_id": i % 2, "plant_id": 100 + i} for i in (5, 3, 1, 4, 2)
]
PEOPLE = [
    {"id": 1, "name": "Ada", "email": "ada@salk.edu"},
    {"id": 2, "name": "Ben", "email": "ben@salk.edu"},
]
TRAITS = [
    {"scan_id": 10, "source_id": 1, "trait_id": 7, "value": 0.5, "is_latest": False},
    {"scan_id": 10, "source_id": 2, "trait_id": 7, "value": 0.6, "is_latest": True},
]
GENE_CANDIDATES = [
    {
        "gene": "AT1G01010",
        "status": "validated",
        "status_logs": [
            {"userid": "ada@salk.edu", "status": "suspected", "date": "2026-01-02"},
            {"userid": "Ben@Salk.EDU", "status": "validated", "date": "2026-02-03"},
        ],
        "legacy_progress_logs_jsonb": {"note": "sent to ben.f@salk.edu", "n": 1},
    }
]


@pytest.fixture
def reader():
    # A copy per test, so a test that edits rows can't leak into the next.
    fake = FakeTableReader(
        copy.deepcopy(
            {
                "species": SPECIES,
                "people": PEOPLE,
                "cyl_scan_traits": TRAITS,
                "cyl_scan_traits_source": TRAITS,
                "gene_candidates": GENE_CANDIDATES,
                "genes": GENES,
                "scrna_cluster_stats": CLUSTER_STATS,
                "cyl_scans_extended": SCANS_EXTENDED,
                "checkpoints": [{"x": 1}],
            }
        ),
        estimates={"species": 120, "people": 2, "cyl_scan_traits_source": 31_800_000},
    )
    previous = _ports.table_reader()
    _ports.configure(table_reader=fake)
    yield fake
    _ports.configure(table_reader=previous)
