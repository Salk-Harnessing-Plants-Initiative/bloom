"""bloommcp's read-only function tools against the live stack.

Every function bloom_agent can execute is classified; an allow-listed one can't write, runs
with the caller's rights and returns rows sorted by columns it really has.
"""

from __future__ import annotations

import pytest

from tests.integration.test_bloommcp_data_tools import _run_in_bloommcp

_ACCESS_SCRIPT = """
import json
from bloom_mcp.data_access import function_access as fa
print(json.dumps({
    "readable": {n: [s.column for s in f.order] for n, f in fa.READABLE_FUNCTIONS.items()},
    "excluded": sorted(fa.EXCLUDED_FUNCTIONS),
}))
"""

_CALL_SCRIPT = """
import json
from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.sections.bloom_data.call_function import call_function
from bloom_mcp.sections.bloom_data.list_functions import list_functions

def call(name, args):
    try:
        return call_function({"function": name, "args": args}).model_dump()
    except BloomMCPError as exc:
        return exc.to_dict()

print(json.dumps({
    "listed": [f.name for f in list_functions({}).functions],
    "search": call("search_genes", {"partial_id": "TEST_BLOOMMCP_NO_SUCH"}),
    "counts": call("get_experiment_summary_counts", {}),
    "genes": call("search_genes", {"partial_id": "AT1G"}),
    "page_input": call("search_genes", {"partial_id": "AT1G", "max_results": 5}),
    "experiments": call("cyl_experiment_search", {"p_query": "x"}),
    "nearest": call("knn_search_esm2", {"query_uid": "x", "match_count": 1001}),
    "writer": call("insert_cyl_qc_codes", {"qc_codes": []}),
    "bad_arg": call("search_genes", {"partial_id": "x", "p_nope": 1}),
    "raised": call("get_trait_recipe_coverage", {}),
    "bad_type": call("get_experiment_traits", {"experiment_id_": "abc"}),
}, default=str))
"""

_EXECUTABLE = """
SELECT p.proname, p.provolatile, p.prosecdef, p.proretset,
       coalesce(
         (SELECT array_agg(name ORDER BY i)
            FROM unnest(p.proargnames, p.proargmodes) WITH ORDINALITY AS a(name, mode, i)
           WHERE mode = 't'),
         '{}')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'public' AND p.prokind = 'f'
   AND has_function_privilege('bloom_agent', p.oid, 'EXECUTE')
   AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')
"""


@pytest.fixture(scope="module")
def access() -> dict:
    return _run_in_bloommcp(_ACCESS_SCRIPT)


@pytest.fixture
def executable(pg_conn) -> dict[str, tuple]:
    with pg_conn.cursor() as cur:
        cur.execute(_EXECUTABLE)
        return {name: rest for name, *rest in cur.fetchall()}


@pytest.fixture(scope="module")
def calls() -> dict:
    return _run_in_bloommcp(_CALL_SCRIPT)


def test_every_function_bloom_agent_can_execute_is_classified(access, executable):
    known = set(access["readable"]) | set(access["excluded"])
    assert sorted(set(executable) - known) == [], "classify these in function_access.py"


def test_every_allow_listed_function_exists_and_can_be_executed(access, executable):
    assert sorted(set(access["readable"]) - set(executable)) == []


def test_no_allow_listed_function_can_write_or_runs_with_owner_rights(
    access, executable
):
    bad = sorted(
        name
        for name in access["readable"]
        if executable[name][0] == "v" or executable[name][1]
    )
    assert bad == [], f"VOLATILE or SECURITY DEFINER: {bad}"


def test_every_allow_listed_function_returns_rows_it_can_be_sorted_by(
    access, executable
):
    bad = {}
    for name, order in access["readable"].items():
        _, _, returns_set, columns = executable[name]
        if not returns_set or not set(order) <= set(columns):
            bad[name] = (returns_set, order, columns)
    assert bad == {}


def test_list_functions_shows_every_allow_listed_function(access, calls):
    assert sorted(calls["listed"]) == sorted(access["readable"])


def test_a_search_with_no_match_returns_no_rows(calls):
    assert calls["search"]["returned"] == 0 and calls["search"]["has_more"] is False
    assert calls["search"]["search_limit_reached"] is False


def test_a_search_sets_its_own_row_limit(calls):
    assert calls["page_input"]["code"] == "invalid_input"
    assert "max_results" in calls["page_input"]["message"]


def test_the_capped_experiment_search_is_refused(calls):
    assert calls["experiments"]["code"] == "invalid_input"


def test_a_top_n_count_over_1000_is_refused(calls):
    assert calls["nearest"]["code"] == "invalid_input"


def test_functions_with_and_without_inputs_answer(calls):
    assert "rows" in calls["counts"] and "rows" in calls["genes"]


def test_a_function_that_can_write_is_refused(calls):
    assert calls["writer"]["code"] == "invalid_input"


def test_an_unknown_input_is_refused(calls):
    assert calls["bad_arg"]["code"] == "invalid_input"
    assert "p_nope" in calls["bad_arg"]["message"]


def test_a_functions_own_message_reaches_claude(calls):
    assert calls["raised"]["code"] == "invalid_input"
    assert calls["raised"]["message"].startswith("give experiment_ids_")


def test_a_wrong_type_input_gets_function_wording(calls):
    assert calls["bad_type"]["code"] == "invalid_input"
    assert "input" in calls["bad_type"]["message"]
    assert "22P02" not in str(calls["bad_type"])
