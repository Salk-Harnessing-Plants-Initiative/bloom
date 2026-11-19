"""The reader through the real postgrest-py client: the request it sends, and how it reads
PostgREST's replies (Content-Range, error bodies), with only the network replaced."""

from __future__ import annotations

import json
from urllib.parse import parse_qsl

import httpx
import pytest
from postgrest import SyncPostgrestClient

from bloom_mcp.data_access.table_ports import (
    BadFilterValue,
    Column,
    ColumnsChanged,
    DatabaseBusy,
    DatabaseUnavailable,
    Filter,
    Function,
    FunctionRejected,
    Param,
    QueryTimedOut,
    RequestTooLong,
    Sort,
    TableReadError,
)
from bloom_mcp.data_access.table_reader import PostgrestTableReader

BASE = "http://pg.test/rest/v1"


class _Supabase:
    """The two parts of supabase-py's client the reader uses."""

    def __init__(self, postgrest):
        self.postgrest = postgrest

    def table(self, name):
        return self.postgrest.from_(name)

    def rpc(self, name, args):
        return self.postgrest.rpc(name, args)


def _reader(reply):
    sent: list[httpx.Request] = []

    def handle(request):
        sent.append(request)
        return reply

    http = httpx.Client(base_url=BASE, transport=httpx.MockTransport(handle))
    client = _Supabase(SyncPostgrestClient(BASE, http_client=http))
    return PostgrestTableReader(client_factory=lambda: client), sent


def _pg_error(code, message):
    """PostgREST's error body: always these four keys."""
    return {"code": code, "message": message, "details": None, "hint": None}


def _params(request):
    return parse_qsl(request.url.query.decode(), keep_blank_values=True)


def test_a_select_sends_columns_filters_order_and_page():
    reader, sent = _reader(httpx.Response(200, json=[{"id": 1}]))
    rows = reader.select(
        "species",
        ["id", "common_name"],
        [
            Filter("common_name", "contains", "a*b"),
            Filter("id", "in", [1, 'x"y']),
            Filter("id", "gte", 2),
            Filter("deleted_at", "is_null", False),
        ],
        [Sort("common_name", descending=True), Sort("id")],
        10,
        20,
    )
    assert rows == [{"id": 1}]
    request = sent[0]
    assert request.method == "GET" and request.url.path == "/rest/v1/species"
    assert _params(request) == [
        ("select", "id,common_name"),
        ("common_name", r"imatch.a\*b"),
        ("id", r'in.("1","x\"y")'),
        ("id", "gte.2"),
        ("deleted_at", "not.is.null"),
        ("order", "common_name.desc,id.asc"),
        ("offset", "20"),
        ("limit", "10"),
    ]


def test_case_insensitive_equals_and_prefix_are_anchored_and_escaped():
    reader, sent = _reader(httpx.Response(200, json=[]))
    reader.select(
        "genes",
        ["gene_id"],
        [
            Filter("symbol", "iequals", "MYB-41*"),
            Filter("gene_id", "istarts_with", "AT4G28110."),
        ],
        [Sort("gene_id")],
        10,
        0,
    )
    params = dict(_params(sent[0]))
    assert params["symbol"] == r"imatch.^MYB\-41\*$"
    assert params["gene_id"] == r"imatch.^AT4G28110\."


@pytest.mark.parametrize(("exact", "mode"), [(True, "exact"), (False, "planned")])
def test_a_count_reads_the_total_from_content_range(exact, mode):
    reader, sent = _reader(
        httpx.Response(200, json=[{"id": 1}], headers={"Content-Range": "0-0/31800000"})
    )
    assert (
        reader.count("cyl_scan_traits_source", "scan_id", [], exact=exact) == 31_800_000
    )
    request = sent[0]
    assert request.method == "GET"
    assert request.headers["prefer"] == f"count={mode}"
    assert _params(request) == [("select", "scan_id"), ("limit", "1")]


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (
            httpx.Response(500, json=_pg_error("57014", "canceling statement")),
            QueryTimedOut,
        ),
        (
            httpx.Response(400, json=_pg_error("42703", "column x does not exist")),
            ColumnsChanged,
        ),
        (
            httpx.Response(400, json=_pg_error("22P02", "invalid input syntax")),
            BadFilterValue,
        ),
        (httpx.Response(414, text="URI too long\n"), RequestTooLong),
        (httpx.Response(503, text="Service Unavailable\n"), DatabaseUnavailable),
        (
            httpx.Response(503, json=_pg_error("PGRST001", "connection refused")),
            DatabaseUnavailable,
        ),
        (
            httpx.Response(504, json=_pg_error("PGRST003", "timed out acquiring")),
            DatabaseBusy,
        ),
        (
            httpx.Response(403, json=_pg_error("42501", "permission denied for x")),
            TableReadError,
        ),
    ],
    ids=[
        "timeout",
        "dropped column",
        "bad value",
        "url too long",
        "gateway 503",
        "connection refused",
        "pool timeout",
        "refused",
    ],
)
def test_postgrest_error_replies_map_to_their_errors(reply, error):
    reader, _ = _reader(reply)
    with pytest.raises(error) as err:
        reader.select("species", ["id"], [], [Sort("id")], 1, 0)
    assert type(err.value) is error
    assert "permission" not in str(err.value)


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        (
            "get_trait_recipe_coverage: give experiment_ids_, scan_ids_, or both",
            "give experiment_ids_, scan_ids_, or both",
        ),
        ("two\nlines   here", "two lines here"),
        ("x" * 500, "x" * 199 + "…"),
        ("", "The function refused these inputs."),
    ],
    ids=["own message", "one line", "capped", "empty"],
)
def test_a_functions_own_message_comes_back_short(message, reason):
    reader, _ = _reader(httpx.Response(400, json=_pg_error("P0001", message)))
    with pytest.raises(FunctionRejected) as err:
        reader.call("list_trait_recipes", {}, [Sort("recipe_key")], 1, 0)
    assert err.value.reason == reason


_SCHEMA = {
    "paths": {
        "/rpc/get_experiment_traits": {
            "post": {
                "parameters": [
                    {
                        "in": "body",
                        "name": "args",
                        "schema": {
                            "properties": {
                                "experiment_id_": {
                                    "format": "bigint",
                                    "type": "integer",
                                },
                                "scan_ids_": {"format": "bigint[]", "type": "array"},
                            },
                            "required": ["experiment_id_"],
                        },
                    },
                    {"$ref": "#/parameters/preferParams"},
                ]
            }
        },
        "/species": {"get": {}},
    },
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
    },
}


def test_columns_are_read_from_postgrest_root_with_types_and_foreign_keys():
    reader, sent = _reader(httpx.Response(200, json=_SCHEMA))
    assert reader.table_columns()["cyl_scans"] == [
        Column("id", "bigint"),
        Column("plant_id", "bigint", "cyl_plants.id"),
        Column("metadata", "jsonb"),
        Column("odd", "unknown"),
    ]
    assert sent[0].method == "GET" and sent[0].url.path == "/rest/v1/"


@pytest.mark.parametrize(
    "reply",
    [httpx.Response(503, text="unavailable"), httpx.Response(200, text="not json")],
    ids=["status", "body"],
)
def test_an_unreadable_schema_is_database_unavailable(reply):
    reader, _ = _reader(reply)
    with pytest.raises(DatabaseUnavailable):
        reader.table_columns()


def test_functions_are_read_from_the_schema_with_their_parameters():
    reader, _ = _reader(httpx.Response(200, json=_SCHEMA))
    assert reader.functions() == {
        "get_experiment_traits": Function(
            "get_experiment_traits",
            (
                Param("experiment_id_", "bigint", True),
                Param("scan_ids_", "bigint[]", False),
            ),
        )
    }


def test_a_call_posts_its_inputs_and_sends_order_and_page():
    reader, sent = _reader(httpx.Response(200, json=[{"scan_id": 1}]))
    rows = reader.call(
        "get_experiment_traits",
        {"experiment_id_": 7, "scan_ids_": [1, 2]},
        [Sort("scan_id"), Sort("similarity", descending=True)],
        10,
        20,
    )
    assert rows == [{"scan_id": 1}]
    request = sent[0]
    assert request.method == "POST"
    assert request.url.path == "/rest/v1/rpc/get_experiment_traits"
    assert json.loads(request.content) == {"experiment_id_": 7, "scan_ids_": [1, 2]}
    assert _params(request) == [
        ("order", "scan_id.asc,similarity.desc"),
        ("offset", "20"),
        ("limit", "10"),
    ]


def test_a_failed_read_logs_only_its_code(caplog):
    reader, _ = _reader(
        httpx.Response(400, json=_pg_error("42501", "permission denied: secret-value"))
    )
    with pytest.raises(TableReadError):
        reader.select("species", ["id"], [], [Sort("id")], 1, 0)
    assert "42501" in caplog.text and "secret-value" not in caplog.text
