"""
Integration tests for get_experiment_traits' query plan (bloom#865, PR before PR A of
add-cyl-trait-csv-export).

The function's reads join nine or ten relations once cyl_scan_traits_source's view is
expanded. Above the default from_collapse_limit (8) the view is planned on its own, so
the batch's scan ids never reach cyl_scan_traits: staging read all 28.9M rows for a
10-scan batch (14.1 s, over the 8 s statement_timeout). Migration
20261001180000_get_experiment_traits_collapse_limits raises both collapse limits on the
function, which lets the scan filter reach the index (50 ms).

These tests read the plan of the function's inner query through auto_explain (loadable
by supabase_admin) at notice level, with sequential scans disabled so that a missing
scan_id index condition cannot hide behind a cheap seq scan on a small table. LOCAL
ONLY: every test rolls back.
"""

import json

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.test_cyl_trait_recipes_read import Fixture  # noqa: E402

SIG = "public.get_experiment_traits(bigint, bigint, text, text, bigint[])"


@pytest.fixture
def fx(pg_conn):
    with pg_conn.cursor() as cur:
        yield Fixture(cur)


def _plans(cur, exp, recipe_key, scan_ids):
    """Run get_experiment_traits as bloom_user and return the nested statements' plans."""
    notices = []
    handler = lambda d: notices.append(d.message_primary)  # noqa: E731
    cur.connection.add_notice_handler(handler)
    try:
        cur.execute("LOAD 'auto_explain'")
        for setting, value in (
            ("auto_explain.log_min_duration", "0"),
            ("auto_explain.log_nested_statements", "on"),
            ("auto_explain.log_level", "notice"),
            ("auto_explain.log_format", "json"),
            ("client_min_messages", "notice"),
            ("enable_seqscan", "off"),
        ):
            cur.execute(f"SET LOCAL {setting} = {value}")
        cur.execute("SET LOCAL ROLE bloom_user")
        cur.execute(
            "SELECT count(*) FROM get_experiment_traits(%s, NULL, NULL, %s, %s)",
            (exp, recipe_key, scan_ids),
        )
        cur.fetchone()
        cur.execute("RESET ROLE")
    finally:
        cur.connection.remove_notice_handler(handler)
    plans = []
    for msg in notices:
        if "plan:" in msg:
            plans.append(json.loads(msg.split("plan:", 1)[1])["Plan"])
    return plans


def _nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def _trait_scans(plans):
    """Every cyl_scan_traits scan node in the plans that read trait rows."""
    return [
        node
        for plan in plans
        for node in _nodes(plan)
        if node.get("Relation Name") == "cyl_scan_traits"
    ]


@pytest.mark.parametrize("mode", ["recipe", "latest"])
def test_scan_filter_reaches_cyl_scan_traits(fx, mode):
    key = fx.K["K1"] if mode == "recipe" else None
    scans = [fx.s["a"], fx.s["b"]]
    nodes = _trait_scans(_plans(fx.cur, fx.E1, key, scans))
    assert nodes, "no cyl_scan_traits scan in the function's plans"
    for node in nodes:
        cond = node.get("Index Cond", "")
        assert "scan_id" in cond, (
            f"{mode}: cyl_scan_traits read as {node['Node Type']} with no scan_id "
            f"index condition ({cond or 'none'}); the scan filter did not reach it"
        )


def test_collapse_limits_are_set_on_the_function(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT proconfig FROM pg_proc WHERE oid = %s::regprocedure", (SIG,)
        )
        config = set(cur.fetchone()[0] or [])
    assert {"join_collapse_limit=12", "from_collapse_limit=12"} <= config, config
