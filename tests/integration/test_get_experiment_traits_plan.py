"""
Integration tests for get_experiment_traits' query plan (bloom#865).

The function's reads join nine or ten relations once cyl_scan_traits_source's view is
expanded, and every join is an explicit JOIN. Above the default join_collapse_limit (8)
the view is planned on its own, so a batch's scan ids, which arrive only through a
join, never reach cyl_scan_traits: staging read all 28.9M rows for a 10-scan batch
(14.1 s, over the 8 s statement_timeout). Migration
20261001180000_get_experiment_traits_collapse_limits sets join_collapse_limit = 11 and
plan_cache_mode = force_custom_plan on the function (50 ms at the same batch).

The tests read the function's main statement, the one that reads cyl_scan_traits_source,
through auto_explain with log_analyze at notice level, and bound the cyl_scan_traits
rows it actually read by the trait rows of the selected scans. Sequential scans are off
so the small fixture cannot hide a full read behind a cheap seq scan. LOCAL ONLY: every
test rolls back.
"""

import json

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.test_cyl_trait_recipes_read import Fixture  # noqa: E402

SIG = "public.get_experiment_traits(bigint, bigint, text, text, bigint[])"
# More than 5 calls, so plpgsql would have moved to a generic plan by the last one.
REPEAT_CALLS = 7


@pytest.fixture
def fx(pg_conn):
    with pg_conn.cursor() as cur:
        yield Fixture(cur)


def _main_plans(cur, args, *, calls=1, session=()):
    """Call get_experiment_traits(*args) as bloom_user `calls` times; return the plan of
    the function's main statement on each call."""
    notices = []
    handler = lambda d: notices.append(d.message_primary)  # noqa: E731
    cur.connection.add_notice_handler(handler)
    try:
        # A no-op where auto_explain is in shared_preload_libraries (as in this image).
        cur.execute("LOAD 'auto_explain'")
        for setting, value in (
            ("auto_explain.log_min_duration", "0"),
            ("auto_explain.log_nested_statements", "on"),
            ("auto_explain.log_analyze", "on"),
            ("auto_explain.log_timing", "off"),
            ("auto_explain.log_level", "notice"),
            ("auto_explain.log_format", "json"),
            ("client_min_messages", "notice"),
            ("enable_seqscan", "off"),
            *session,
        ):
            cur.execute(f"SET LOCAL {setting} = {value}")
        cur.execute("SET LOCAL ROLE bloom_user")
        try:
            for _ in range(calls):
                cur.execute(
                    "SELECT count(*) FROM get_experiment_traits(%s, %s, %s, %s, %s)",
                    args,
                )
                cur.fetchone()
        finally:
            cur.execute("RESET ROLE")
    finally:
        cur.connection.remove_notice_handler(handler)
    plans = []
    for msg in notices:
        if not msg.startswith("duration:"):
            continue
        logged = json.loads(msg.split("plan:", 1)[1])
        if "cyl_scan_traits_source" in logged["Query Text"]:
            plans.append(logged["Plan"])
    assert (
        len(plans) == calls
    ), f"expected {calls} main-statement plans, got {len(plans)}"
    return plans


def _nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def _rows_read(plan):
    """A lower bound on the cyl_scan_traits rows the plan read, over all loops of every
    scan of it. EXPLAIN reports Actual Rows as a per-loop average rounded to an integer,
    so the true total is at least (rows - 0.5) * loops."""
    nodes = [n for n in _nodes(plan) if n.get("Relation Name") == "cyl_scan_traits"]
    assert nodes, "the main statement does not read cyl_scan_traits"
    return sum(
        max(n["Actual Rows"] - 0.5, 0) * n["Actual Loops"]
        for n in nodes
        if n["Actual Loops"]
    )


def _trait_rows(cur, scan_ids):
    cur.execute(
        "SELECT count(*) FROM cyl_scan_traits WHERE scan_id = ANY (%s)", (scan_ids,)
    )
    return cur.fetchone()[0]


def _experiment_scans(cur, exp):
    cur.execute(
        "SELECT array_agg(s.id) FROM cyl_scans s"
        " JOIN cyl_plants p ON p.id = s.plant_id"
        " JOIN cyl_waves w ON w.id = p.wave_id WHERE w.experiment_id = %s",
        (exp,),
    )
    return cur.fetchone()[0]


def _cases(fx):
    batch = [fx.s["a"], fx.s["b"]]
    return {
        "recipe": ((fx.E1, None, None, fx.K["K1"], batch), batch),
        "latest": ((fx.E1, None, None, None, batch), batch),
        "source": ((fx.E1, fx.src[10], None, None, batch), batch),
        "latest_whole_experiment": (
            (fx.E1, None, None, None, None),
            _experiment_scans(fx.cur, fx.E1),
        ),
    }


@pytest.mark.parametrize(
    "case", ["recipe", "latest", "source", "latest_whole_experiment"]
)
def test_reads_only_the_selected_scans_trait_rows(fx, case):
    args, scans = _cases(fx)[case]
    bound = _trait_rows(fx.cur, scans)
    (plan,) = _main_plans(fx.cur, args)
    read = _rows_read(plan)
    assert (
        read <= bound
    ), f"{case}: read {read} cyl_scan_traits rows; the selected scans have {bound}"


@pytest.mark.parametrize("case", ["recipe", "latest"])
def test_later_calls_in_a_session_still_read_only_the_batch(fx, case):
    # Each PostgREST connection is reused, and plpgsql may switch to a generic plan
    # after five calls; force_generic_plan would be that plan on every call.
    args, scans = _cases(fx)[case]
    bound = _trait_rows(fx.cur, scans)
    plans = _main_plans(
        fx.cur,
        args,
        calls=REPEAT_CALLS,
        session=(("plan_cache_mode", "force_generic_plan"),),
    )
    reads = [_rows_read(p) for p in plans]
    assert max(reads) <= bound, f"{case}: rows read per call {reads}; bound {bound}"


def test_planner_settings_on_the_function(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT proconfig FROM pg_proc WHERE oid = %s::regprocedure", (SIG,)
        )
        config = set(cur.fetchone()[0] or [])
    assert {"join_collapse_limit=11", "plan_cache_mode=force_custom_plan"} <= config
    assert not any(c.startswith("from_collapse_limit=") for c in config), config
