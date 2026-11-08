"""The reader through the real postgrest-py client: the request it sends, and how it reads
PostgREST's replies (Content-Range, error bodies), with only the network replaced."""

from __future__ import annotations

from urllib.parse import parse_qsl

import httpx
import pytest
from postgrest import SyncPostgrestClient

from bloom_mcp.data_access.table_ports import (
    BadFilterValue,
    ColumnsChanged,
    Filter,
    QueryTimedOut,
    RequestTooLong,
    Sort,
    TableReadError,
)
from bloom_mcp.data_access.table_reader import PostgrestTableReader

BASE = "http://pg.test/rest/v1"


def _reader(reply):
    sent: list[httpx.Request] = []

    def handle(request):
        sent.append(request)
        return reply

    http = httpx.Client(base_url=BASE, transport=httpx.MockTransport(handle))
    client = SyncPostgrestClient(BASE, http_client=http)
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
        (
            httpx.Response(403, json=_pg_error("42501", "permission denied for x")),
            TableReadError,
        ),
    ],
    ids=["timeout", "dropped column", "bad value", "url too long", "refused"],
)
def test_postgrest_error_replies_map_to_their_errors(reply, error):
    reader, _ = _reader(reply)
    with pytest.raises(error) as err:
        reader.select("species", ["id"], [], [Sort("id")], 1, 0)
    assert type(err.value) is error
    assert "permission" not in str(err.value)
