"""The PostgREST adapter: filters map to PostgREST calls, and errors are mapped safely."""

from __future__ import annotations

import httpx
import pytest
from postgrest.exceptions import APIError
from postgrest.types import CountMethod

from bloom_mcp.data_access.table_ports import (
    BadFilterValue,
    Column,
    DatabaseUnavailable,
    Filter,
    QueryTimedOut,
    Sort,
    TableReadError,
)
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
        order=[Sort("dataset_id", descending=True), Sort("cluster_id")],
        limit=10,
        offset=20,
    )
    assert rows == [{"id": 1}]
    calls = [(name, args) for name, args, _ in client.log]
    assert ("select", ("id,common_name",)) in calls
    assert ("gte", ("id", 2)) in calls
    assert ("filter", ("id", "in", '("2","3")')) in calls
    assert ("filter", ("common_name", "imatch", r"a_b\%")) in calls
    assert ("is_", ("deleted_at", "null")) in calls
    assert ("not_", ()) in calls and ("is_", ("common_name", "null")) in calls
    orders = [(args, kwargs) for name, args, kwargs in client.log if name == "order"]
    assert orders == [
        (("dataset_id",), {"desc": True}),
        (("cluster_id",), {"desc": False}),
    ]
    assert ("range", (20, 29)) in calls


def test_statement_timeout_becomes_query_timed_out():
    client = _Client(
        error=APIError(
            {"code": "57014", "message": "canceling statement due to statement timeout"}
        )
    )
    with pytest.raises(QueryTimedOut):
        _reader(client).select("species", ["id"], [], [Sort("id")], 10, 0)


def test_other_database_errors_keep_internals_out_of_the_message():
    client = _Client(
        error=APIError(
            {"code": "42501", "message": "permission denied for table secret_x"}
        )
    )
    with pytest.raises(TableReadError) as err:
        _reader(client).select("species", ["id"], [], [Sort("id")], 10, 0)
    assert type(err.value) is TableReadError
    assert "secret_x" not in str(err.value)
    assert "42501" not in str(err.value)


def test_contains_takes_every_symbol_literally():
    client = _Client(_Result(data=[]))
    _reader(client).select(
        "species", ["id"], [Filter("common_name", "contains", "Col*0 (x).é")], [], 1, 0
    )
    calls = [(name, args) for name, args, _ in client.log]
    assert ("filter", ("common_name", "imatch", r"Col\*0 \(x\)\.é")) in calls


def test_in_quotes_each_value_and_escapes_quotes_and_backslashes():
    client = _Client(_Result(data=[]))
    _reader(client).select(
        "species",
        ["id"],
        [Filter("common_name", "in", ['a"b', "c\\d", "e,f"])],
        [],
        1,
        0,
    )
    calls = [(name, args) for name, args, _ in client.log]
    assert ("filter", ("common_name", "in", r'("a\"b","c\\d","e,f")')) in calls


@pytest.mark.parametrize("code", ["22P02", "22003", "22007", "22008", "42883"])
def test_a_filter_that_does_not_fit_its_column_becomes_bad_filter_value(code):
    client = _Client(error=APIError({"code": code, "message": "invalid input syntax"}))
    with pytest.raises(BadFilterValue) as err:
        _reader(client).select("species", ["id"], [], [Sort("id")], 10, 0)
    assert code not in str(err.value)


def test_an_unreachable_database_becomes_database_unavailable():
    client = _Client(error=httpx.ConnectError("connection refused to db:5432"))
    with pytest.raises(DatabaseUnavailable) as err:
        _reader(client).count("species", "id", [], exact=True)
    assert "5432" not in str(err.value)


@pytest.mark.parametrize("exact", [True, False])
def test_count_is_a_get_of_one_row_in_the_requested_mode(exact):
    client = _Client(_Result(data=[], count=7))
    assert _reader(client).count("species", "id", [], exact=exact) == 7
    select = next(c for c in client.log if c[0] == "select")
    mode = CountMethod.exact if exact else CountMethod.planned
    assert select[1] == ("id",) and select[2] == {"count": mode}
    assert ("limit", (1,)) in [(n, a) for n, a, _ in client.log]


def test_a_count_that_hits_the_statement_timeout_becomes_query_timed_out():
    client = _Client(
        error=APIError(
            {"code": "57014", "message": "canceling statement due to timeout"}
        )
    )
    with pytest.raises(QueryTimedOut):
        _reader(client).count("cyl_scan_traits", "id", [], exact=True)


def test_columns_come_with_their_type_and_foreign_key(monkeypatch):
    spec = {
        "definitions": {
            "cyl_scans": {
                "properties": {
                    "id": {
                        "description": "Note:\nThis is a Primary Key.<pk/>",
                        "format": "bigint",
                        "type": "integer",
                    },
                    "plant_id": {
                        "description": "Note:\nThis is a Foreign Key to `cyl_plants.id`."
                        "<fk table='cyl_plants' column='id'/>",
                        "format": "bigint",
                        "type": "integer",
                    },
                    "metadata": {"format": "jsonb"},
                    "odd": {},
                }
            }
        }
    }

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return spec

    monkeypatch.setattr(
        "bloom_mcp.supabase_client.require_env", lambda: ("http://x", "k")
    )
    monkeypatch.setattr("httpx.get", lambda *a, **k: _Response())
    columns = PostgrestTableReader(client_factory=lambda: None).table_columns()
    assert columns["cyl_scans"] == [
        Column("id", "bigint"),
        Column("plant_id", "bigint", "cyl_plants.id"),
        Column("metadata", "jsonb"),
        Column("odd", "unknown"),
    ]
