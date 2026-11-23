"""FakeTableReader compares and sorts NaN as Postgres does: above every number."""

from __future__ import annotations

from bloom_mcp.data_access.fake_table_reader import FakeTableReader
from bloom_mcp.data_access.table_ports import Filter, Sort

ROWS = [
    {"id": 1, "log2fc": 1.0},
    {"id": 2, "log2fc": "NaN"},
    {"id": 3, "log2fc": -1.0},
    {"id": 4, "log2fc": "Infinity"},
    {"id": 5, "log2fc": None},
]


def _ids(filters, order=()):
    reader = FakeTableReader({"t": [dict(r) for r in ROWS]})
    rows = reader.select("t", ["id"], list(filters), list(order), 100, 0)
    return [r["id"] for r in rows]


def test_nan_is_greater_than_every_number():
    assert _ids([Filter("log2fc", "gt", 0.5)]) == [1, 2, 4]


def test_nan_is_never_less_than_a_number():
    assert _ids([Filter("log2fc", "lt", -0.5)]) == [3]


def test_nan_equals_nan():
    assert _ids([Filter("log2fc", "eq", float("nan"))]) == [2]
    assert _ids([Filter("log2fc", "neq", float("nan"))]) == [1, 3, 4]
    # As PostgREST is sent it: NaN written as text.
    assert _ids([Filter("log2fc", "neq", "NaN")]) == [1, 3, 4]


def test_nan_sorts_after_infinity_and_before_null():
    assert _ids([], [Sort("log2fc")]) == [3, 1, 4, 2, 5]
    assert _ids([], [Sort("log2fc", descending=True)]) == [5, 2, 4, 1, 3]


def test_a_text_cell_spelled_nan_stays_text_against_a_text_value():
    reader = FakeTableReader({"t": [{"id": 1, "name": "NaN"}, {"id": 2, "name": "b"}]})
    rows = reader.select("t", ["id"], [Filter("name", "gt", "A")], [], 100, 0)
    assert [r["id"] for r in rows] == [1, 2]


def test_a_text_column_with_a_cell_spelled_nan_sorts_as_text():
    rows = [{"id": 1, "name": "b"}, {"id": 2, "name": "NaN"}, {"id": 3, "name": "a"}]
    reader = FakeTableReader({"t": rows})
    found = reader.select("t", ["id"], [], [Sort("name")], 100, 0)
    assert [r["id"] for r in found] == [2, 3, 1]
