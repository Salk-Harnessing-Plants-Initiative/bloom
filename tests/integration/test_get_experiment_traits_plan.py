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
so the small fixture cannot hide a full read behind a cheap seq scan. Background
experiments and a fresh ANALYZE give the planner a realistically shaped table: on the
few hundred rows other tests leave committed, a full read is the cheapest plan, and the
plan then depends on when autovacuum last analyzed. LOCAL ONLY: every test rolls back.
"""

import json
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.test_cyl_trait_recipes_read import Fixture  # noqa: E402

SIG = "public.get_experiment_traits(bigint, bigint, text, text, bigint[])"
# More than 5 calls, so plpgsql would have moved to a generic plan by the last one.
REPEAT_CALLS = 7
BACKGROUND_EXPERIMENTS = 100
BACKGROUND_SCANS_PER_EXPERIMENT = 30
ANALYZED_TABLES = (
    "cyl_experiments",
    "cyl_waves",
    "cyl_plants",
    "accessions",
    "cyl_scans",
    "cyl_traits",
    "cyl_trait_sources",
    "cyl_scan_traits",
    "cyl_scan_latest_source",
)


def _seed_background(cur, trait_ids):
    """Experiments of one-source scans with every fixture trait, then ANALYZE."""
    tag = f"plan-bg-{uuid.uuid4().hex[:12]}"
    cur.execute(
        """
        WITH sp AS (INSERT INTO species DEFAULT VALUES RETURNING id),
        e AS (
            INSERT INTO cyl_experiments (name, species_id)
            SELECT %(tag)s || '-' || g, (SELECT id FROM sp) FROM generate_series(1, %(n_exp)s) g
            RETURNING id
        ),
        w AS (INSERT INTO cyl_waves (experiment_id, number) SELECT id, 1 FROM e RETURNING id),
        a AS (INSERT INTO accessions (name) VALUES (%(tag)s) RETURNING id),
        p AS (
            INSERT INTO cyl_plants (wave_id, accession_id, germ_day, qr_code)
            SELECT w.id, (SELECT id FROM a), 5, %(tag)s || '-' || w.id || '-' || g
            FROM w, generate_series(1, %(per_exp)s) g
            RETURNING id
        ),
        s AS (
            INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days)
            SELECT id, '2026-01-01', 10 FROM p RETURNING id
        ),
        src AS (
            INSERT INTO cyl_trait_sources (name, scan_id) SELECT %(tag)s, id FROM s
            RETURNING id, scan_id
        )
        INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value)
        SELECT src.scan_id, src.id, t.id, 1 FROM src, unnest(%(traits)s::int[]) AS t(id)
        """,
        {
            "tag": tag,
            "n_exp": BACKGROUND_EXPERIMENTS,
            "per_exp": BACKGROUND_SCANS_PER_EXPERIMENT,
            "traits": list(trait_ids),
        },
    )
    for table in ANALYZED_TABLES:
        cur.execute(f"ANALYZE public.{table}")


@pytest.fixture
def fx(pg_conn):
    with pg_conn.cursor() as cur:
        fixture = Fixture(cur)
        _seed_background(cur, fixture.trait.values())
        yield fixture


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
    # after five calls; force_generic_plan would be that plan on every call. Seq scans
    # stay allowed here: the generic latest-branch plan full-scans cyl_scan_traits
    # when they are, even with the join flattened.
    args, scans = _cases(fx)[case]
    bound = _trait_rows(fx.cur, scans)
    plans = _main_plans(
        fx.cur,
        args,
        calls=REPEAT_CALLS,
        session=(("plan_cache_mode", "force_generic_plan"), ("enable_seqscan", "on")),
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
