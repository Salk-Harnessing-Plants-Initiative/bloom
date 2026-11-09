"""list_functions and call_function: only allow-listed, read-only functions, checked inputs."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import function_access
from bloom_mcp.data_access.function_access import MAX_SEARCH_ROWS
from bloom_mcp.data_access.table_ports import (
    BadFilterValue,
    DatabaseUnavailable,
    Function,
    FunctionRejected,
    Param,
    QueryTimedOut,
    TableReadError,
)
from bloom_mcp.sections.bloom_data._common import MAX_IN_VALUES
from bloom_mcp.sections.bloom_data._page import DEFAULT_LIMIT
from bloom_mcp.sections.bloom_data.call_function import call_function
from bloom_mcp.sections.bloom_data.list_functions import list_functions

SEARCH = Function(
    "search_genes",
    (Param("partial_id", "text", True), Param("max_results", "integer", False)),
)
NEAREST = Function(
    "knn_search_esm2",
    (Param("query_uid", "text", True), Param("match_count", "integer", False)),
)
TRAITS = Function(
    "get_experiment_traits",
    (Param("experiment_id_", "bigint", True), Param("scan_ids_", "bigint[]", False)),
)
WRITER = Function("insert_image", (Param("species_common_name", "text", True),))
PROTEINS = [{"uid": f"P{i}", "gene_id": f"AT{i}"} for i in (3, 1, 2)]
MANY = [{"uid": f"P{i:04d}"} for i in range(1500)]
SIMILAR = [{"uid": f"Q{i:02d}", "similarity": 1 - i / 100} for i in range(60)]
TRAIT_ROWS = [
    {"scan_id": s, "trait_name": t, "source_id": 1, "trait_value": 0.1}
    for s in (2, 1)
    for t in ("b", "a")
]


@pytest.fixture
def functions(reader):
    reader.function_rows = {
        "search_genes": (SEARCH, PROTEINS),
        "knn_search_esm2": (NEAREST, SIMILAR),
        "get_experiment_traits": (TRAITS, TRAIT_ROWS),
        "insert_image": (WRITER, []),
    }
    reader.limit_inputs = {
        "search_genes": ("max_results", 20),
        "knn_search_esm2": ("match_count", 20),
    }
    return reader


def test_only_allow_listed_functions_are_listed_with_their_inputs(functions):
    listed = {f.name: f for f in list_functions({}).functions}
    assert set(listed) == {"search_genes", "knn_search_esm2", "get_experiment_traits"}
    search = listed["search_genes"]
    assert (
        search.description
        == function_access.READABLE_FUNCTIONS[search.name].description
    )
    # bloommcp sets max_results itself, so Claude isn't shown it.
    assert [(p.name, p.type, p.required) for p in search.params] == [
        ("partial_id", "text", True),
    ]


def test_a_call_returns_rows_in_the_declared_order(functions):
    result = call_function({"function": "search_genes", "args": {"partial_id": "P"}})
    assert [r["uid"] for r in result.rows] == ["P1", "P2", "P3"]
    assert functions.called == [
        ("search_genes", {"partial_id": "P", "max_results": DEFAULT_LIMIT + 1})
    ]


def test_a_call_pages_without_overlap(functions):
    first = call_function(
        {"function": "get_experiment_traits", "args": {"experiment_id_": 7}, "limit": 3}
    )
    assert first.has_more and first.next_offset == 3
    second = call_function(
        {
            "function": "get_experiment_traits",
            "args": {"experiment_id_": 7},
            "limit": 3,
            "offset": 3,
        }
    )
    keys = [(r["scan_id"], r["trait_name"]) for r in first.rows + second.rows]
    assert keys == [(1, "a"), (1, "b"), (2, "a"), (2, "b")]


def test_a_list_input_is_passed_as_a_list(functions):
    call_function(
        {
            "function": "get_experiment_traits",
            "args": {"experiment_id_": 7, "scan_ids_": [1, 2]},
        }
    )
    assert functions.called[-1][1] == {"experiment_id_": 7, "scan_ids_": [1, 2]}


@pytest.mark.parametrize("name", ["insert_image", "bloom_user_for_email", "nope"])
def test_a_function_off_the_list_is_refused_before_any_call(functions, name):
    with pytest.raises(BloomMCPError) as err:
        call_function({"function": name, "args": {}})
    assert err.value.code == "invalid_input"
    assert "list_functions" in err.value.remedy
    assert functions.called == []


@pytest.mark.parametrize(
    ("args", "says"),
    [
        ({}, "missing partial_id"),
        ({"partial_id": "x", "p_secret": 1}, "no input p_secret"),
        ({"partial_id": {"a": 1}}, "takes text"),
        ({"partial_id": ["x", {"a": 1}]}, "takes text"),
        ({"partial_id": "x", "max_results": 5000}, "no input max_results"),
    ],
    ids=["missing", "unknown", "object", "object in list", "page input"],
)
def test_inputs_are_checked_before_any_call(functions, args, says):
    with pytest.raises(BloomMCPError) as err:
        call_function({"function": "search_genes", "args": args})
    assert err.value.code == "invalid_input"
    assert says in err.value.message
    assert functions.called == []


def test_a_list_input_past_the_limit_is_refused(functions):
    with pytest.raises(BloomMCPError) as err:
        call_function(
            {
                "function": "get_experiment_traits",
                "args": {
                    "experiment_id_": 7,
                    "scan_ids_": list(range(MAX_IN_VALUES + 1)),
                },
            }
        )
    assert str(MAX_IN_VALUES) in err.value.remedy


def test_a_null_input_is_passed_for_optional_arguments(functions):
    call_function(
        {
            "function": "get_experiment_traits",
            "args": {"experiment_id_": 7, "scan_ids_": None},
        }
    )
    assert functions.called[-1][1]["scan_ids_"] is None


def test_an_allow_listed_function_the_api_does_not_serve_is_refused(functions):
    del functions.function_rows["get_experiment_traits"]
    with pytest.raises(BloomMCPError):
        call_function(
            {"function": "get_experiment_traits", "args": {"experiment_id_": 1}}
        )


@pytest.mark.parametrize(
    ("failure", "code", "says"),
    [
        (QueryTimedOut("statement timeout"), "tool_error", "took longer"),
        (BadFilterValue("bad"), "invalid_input", "doesn't fit its type"),
        (TableReadError("refused"), "tool_error", "couldn't run with these inputs"),
        (DatabaseUnavailable("down"), "tool_error", "couldn't be reached"),
    ],
    ids=["timeout", "wrong type", "refused", "unreachable"],
)
def test_a_failed_call_gets_function_wording(functions, failure, code, says):
    functions.fail_with = failure
    with pytest.raises(BloomMCPError) as err:
        call_function({"function": "search_genes", "args": {"partial_id": "x"}})
    assert err.value.code == code and says in err.value.message
    text = f"{err.value.message} {err.value.remedy}"
    assert "filter" not in text and "list_tables" not in text


def test_a_functions_own_message_is_passed_on(functions):
    functions.fail_with = FunctionRejected("give experiment_ids_, scan_ids_, or both")
    with pytest.raises(BloomMCPError) as err:
        call_function({"function": "search_genes", "args": {"partial_id": "x"}})
    assert err.value.code == "invalid_input"
    assert err.value.message == "give experiment_ids_, scan_ids_, or both"
    assert "list_functions" in err.value.remedy


def test_no_function_is_both_readable_and_excluded():
    assert (
        not set(function_access.READABLE_FUNCTIONS) & function_access.EXCLUDED_FUNCTIONS
    )


def _search(functions, offset: int, limit: int = 500):
    functions.function_rows["search_genes"] = (SEARCH, MANY)
    return call_function(
        {
            "function": "search_genes",
            "args": {"partial_id": "P"},
            "limit": limit,
            "offset": offset,
        }
    )


def test_a_search_pages_past_its_default_of_20(functions):
    first = _search(functions, 0, limit=100)
    assert first.returned == 100 and first.has_more
    second = _search(functions, first.next_offset, limit=100)
    assert second.rows[0]["uid"] == "P0100"
    assert not second.search_limit_reached


def test_a_search_says_when_it_stopped_at_its_limit(functions):
    last = _search(functions, 500)
    assert last.returned == MAX_SEARCH_ROWS - 500 and not last.has_more
    assert last.search_limit_reached
    assert _search(functions, MAX_SEARCH_ROWS).search_limit_reached


def test_a_search_with_fewer_matches_than_the_limit_is_complete(functions):
    functions.function_rows["search_genes"] = (SEARCH, MANY[:995])
    last = call_function(
        {
            "function": "search_genes",
            "args": {"partial_id": "P"},
            "limit": 500,
            "offset": 500,
        }
    )
    assert last.returned == 495 and not last.search_limit_reached


def test_a_top_n_search_returns_exactly_the_n_asked_for(functions):
    result = call_function(
        {"function": "knn_search_esm2", "args": {"query_uid": "Q", "match_count": 50}}
    )
    assert result.returned == 50 and not result.has_more
    assert result.rows[0]["uid"] == "Q00"


@pytest.mark.parametrize("count", [0, MAX_SEARCH_ROWS + 1, "50", True, 2.5])
def test_a_top_n_count_outside_1_to_1000_is_refused(functions, count):
    with pytest.raises(BloomMCPError) as err:
        call_function(
            {
                "function": "knn_search_esm2",
                "args": {"query_uid": "Q", "match_count": count},
            }
        )
    assert err.value.code == "invalid_input"
    assert "match_count" in err.value.message
    assert functions.called == []


def test_the_experiment_searches_are_not_callable():
    for name in ("cyl_experiment_search", "gravi_experiment_search"):
        assert not function_access.is_callable(name)
        assert name in function_access.EXCLUDED_FUNCTIONS


def test_every_limit_input_names_one_kind():
    for entry in function_access.READABLE_FUNCTIONS.values():
        assert not (entry.page_input and entry.count_input)
