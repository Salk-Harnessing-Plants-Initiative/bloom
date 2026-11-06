"""The PostgREST adapter: filters map to PostgREST calls, and errors are mapped safely."""

from __future__ import annotations

import pytest
from postgrest.exceptions import APIError

from bloom_mcp.data_access.table_ports import Filter, QueryTimedOut, TableReadError
from bloom_mcp.data_access.table_reader import PostgrestTableReader


class _Query:
    """Records the builder calls supabase-py's query builder would receive."""

    def __init__(self, log, result=None, error=None):
        self.log, self.result, self.error = log, result, error

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.log.append((name, args, kwargs))
            return self

        return call

    @property
    def not_(self):
        self.log.append(("not_", (), {}))
        return self

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class _Result:
    def __init__(self, data=None, count=None):
        self.data, self.count = data, count


class _Client:
    def __init__(self, result=None, error=None):
        self.log = []
        self.result, self.error = result, error

    def table(self, name):
        self.log.append(("table", (name,), {}))
        return _Query(self.log, self.result, self.error)

    def rpc(self, name, params):
        self.log.append(("rpc", (name, params), {}))
        return _Query(self.log, self.result, self.error)


def _reader(client):
    return PostgrestTableReader(client_factory=lambda: client)


def test_select_maps_filters_order_and_range():
    client = _Client(_Result(data=[{"id": 1}]))
    rows = _reader(client).select(
        "species",
        ["id", "common_name"],
        [
            Filter("id", "gte", 2),
            Filter("id", "in", [2, 3]),
            Filter("common_name", "contains", "a_b%"),
            Filter("deleted_at", "is_null", True),
            Filter("common_name", "is_null", False),
        ],
        order_by="id",
        descending=True,
        limit=10,
        offset=20,
    )
    assert rows == [{"id": 1}]
    calls = [(name, args) for name, args, _ in client.log]
    assert ("select", ("id,common_name",)) in calls
    assert ("gte", ("id", 2)) in calls
    assert ("in_", ("id", [2, 3])) in calls
    assert ("ilike", ("common_name", r"%a\_b\%%")) in calls
    assert ("is_", ("deleted_at", "null")) in calls
    assert ("not_", ()) in calls and ("is_", ("common_name", "null")) in calls
    assert ("order", ("id",)) in calls
    assert ("range", (20, 29)) in calls


def test_statement_timeout_becomes_query_timed_out():
    client = _Client(
        error=APIError(
            {"code": "57014", "message": "canceling statement due to statement timeout"}
        )
    )
    with pytest.raises(QueryTimedOut):
        _reader(client).select("species", ["id"], [], None, False, 10, 0)


def test_other_database_errors_keep_internals_out_of_the_message():
    client = _Client(
        error=APIError(
            {"code": "42501", "message": "permission denied for table secret_x"}
        )
    )
    with pytest.raises(TableReadError) as err:
        _reader(client).select("species", ["id"], [], None, False, 10, 0)
    assert "secret_x" not in str(err.value)
    assert "42501" in str(err.value)


def test_count_uses_a_visible_column_and_the_requested_mode():
    client = _Client(_Result(data=[], count=7))
    assert _reader(client).count("species", "id", [], exact=False) == 7
    select = next(c for c in client.log if c[0] == "select")
    assert select[1] == ("id",) and select[2] == {"count": "planned", "head": True}


def test_row_estimates_call_the_rpc():
    client = _Client(_Result(data=[{"table_name": "species", "row_estimate": 5}]))
    estimates = _reader(client).row_estimates(["species", "views_have_none"])
    assert estimates == {"species": 5, "views_have_none": None}
    assert (
        "rpc",
        ("bloom_table_row_estimates", {"p_tables": ["species", "views_have_none"]}),
    ) in [(n, a) for n, a, _ in client.log]
