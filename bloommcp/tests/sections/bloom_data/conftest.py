"""A fake table reader injected for the bloom_data tool tests."""

from __future__ import annotations

import pytest

from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.tools import _ports

SPECIES = [
    {"id": i, "common_name": f"plant {i}", "deleted_at": None} for i in range(1, 121)
]
PEOPLE = [
    {"id": 1, "name": "Ada", "email": "ada@salk.edu"},
    {"id": 2, "name": "Ben", "email": "ben@salk.edu"},
]
TRAITS = [{"id": 1, "scan_id": 10, "value": 0.5}]
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
    fake = FakeTableReader(
        {
            "species": SPECIES,
            "people": PEOPLE,
            "cyl_scan_traits": TRAITS,
            "gene_candidates": GENE_CANDIDATES,
            "checkpoints": [{"x": 1}],
        },
        estimates={"species": 120, "people": 2, "cyl_scan_traits": 31_800_000},
    )
    previous = _ports.table_reader()
    _ports.configure(table_reader=fake)
    yield fake
    _ports.configure(table_reader=previous)
