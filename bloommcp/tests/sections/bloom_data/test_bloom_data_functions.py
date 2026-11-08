"""list_functions and call_function: only allow-listed, read-only functions, checked inputs."""

from __future__ import annotations

import pytest

from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import function_access
from bloom_mcp.data_access.table_ports import Function, Param
from bloom_mcp.sections.bloom_data._common import MAX_IN_VALUES
from bloom_mcp.sections.bloom_data.call_function import call_function
from bloom_mcp.sections.bloom_data.list_functions import list_functions

SEARCH = Function(
    "cyl_experiment_search",
    (Param("p_query", "text", True), Param("p_limit", "integer", False)),
)
TRAITS = Function(
    "get_experiment_traits",
    (Param("experiment_id_", "bigint", True), Param("scan_ids_", "bigint[]", False)),
)
WRITER = Function("insert_image", (Param("species_common_name", "text", True),))
EXPERIMENTS = [{"id": i, "name": f"exp {i}"} for i in (3, 1, 2)]
TRAIT_ROWS = [
    {"scan_id": s, "trait_name": t, "source_id": 1, "trait_value": 0.1}
    for s in (2, 1)
    for t in ("b", "a")
]


@pytest.fixture
def functions(reader):
    reader.function_rows = {
        "cyl_experiment_search": (SEARCH, EXPERIMENTS),
        "get_experiment_traits": (TRAITS, TRAIT_ROWS),
        "insert_image": (WRITER, []),
    }
    return reader


def test_only_allow_listed_functions_are_listed_with_their_inputs(functions):
    listed = {f.name: f for f in list_functions({}).functions}
    assert set(listed) == {"cyl_experiment_search", "get_experiment_traits"}
    search = listed["cyl_experiment_search"]
    assert (
        search.description
        == function_access.READABLE_FUNCTIONS[search.name].description
    )
    assert [(p.name, p.type, p.required) for p in search.params] == [
        ("p_query", "text", True),
        ("p_limit", "integer", False),
    ]


def test_a_call_returns_rows_in_the_declared_order(functions):
    result = call_function(
        {"function": "cyl_experiment_search", "args": {"p_query": "exp"}}
    )
    assert [r["id"] for r in result.rows] == [1, 2, 3]
    assert functions.called == [("cyl_experiment_search", {"p_query": "exp"})]


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
        ({}, "missing p_query"),
        ({"p_query": "x", "p_secret": 1}, "no input p_secret"),
        ({"p_query": {"a": 1}}, "takes text"),
        ({"p_query": ["x", {"a": 1}]}, "takes text"),
    ],
    ids=["missing", "unknown", "object", "object in list"],
)
def test_inputs_are_checked_before_any_call(functions, args, says):
    with pytest.raises(BloomMCPError) as err:
        call_function({"function": "cyl_experiment_search", "args": args})
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


def test_a_failed_call_is_reported_plainly(functions):
    from bloom_mcp.data_access.table_ports import QueryTimedOut

    functions.fail_with = QueryTimedOut("statement timeout")
    with pytest.raises(BloomMCPError) as err:
        call_function({"function": "cyl_experiment_search", "args": {"p_query": "x"}})
    assert err.value.code == "tool_error" and "filter" in err.value.message


def test_no_function_is_both_readable_and_excluded():
    assert (
        not set(function_access.READABLE_FUNCTIONS) & function_access.EXCLUDED_FUNCTIONS
    )
