"""
Integration tests for rnaseq_runs.metadata: request_scrna_cellranger_run stores the
dataset details given with a run, calls without them still work, params stays the
pipeline's inputs, anything but a JSON object of at most 64 KB is refused, only
bloom_workflows may call the function, and the rollback restores the old signature.

Each test builds the schema up to this migration inside its own transaction and rolls it
back, so the database is left unchanged.
"""

import pytest

from tests.integration.test_rnaseq_runs import MIGRATION as RNASEQ_RUNS_MIGRATION
from tests.integration.test_rnaseq_runs import (
    USER,
    _find_one,
    _sql_body,
    _to_cellranger_schema,
)

psycopg = pytest.importorskip("psycopg")
Jsonb = psycopg.types.json.Jsonb

TABLE = "rnaseq_runs"
QUEUE_TABLE = "pgmq.q_rnaseq_dispatch"
OLD_SIG = "public.request_scrna_cellranger_run(text, text, uuid)"
NEW_SIG = "public.request_scrna_cellranger_run(text, text, uuid, jsonb)"
SAMPLE_RULE_MIGRATION = _find_one("migrations", "*_limit_cellranger_sample_names.sql")
MIGRATION = _find_one("migrations", "*_add_rnaseq_run_metadata.sql")
ROLLBACK = _find_one("rollbacks", "*_add_rnaseq_run_metadata_rollback.sql")
METADATA_MAX_BYTES = 65536
DETAILS = {
    "species_id": 1,
    "dataset_name": "Col-0 root tip",
    "accession": "Col-0",
    "attributes": {"tissue": "root tip", "days_after_germination": "7"},
}


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        _to_cellranger_schema(c)
        c.execute(_sql_body(RNASEQ_RUNS_MIGRATION))
        c.execute(_sql_body(SAMPLE_RULE_MIGRATION))
        c.execute(_sql_body(MIGRATION))
        c.execute(f"DELETE FROM {QUEUE_TABLE}")
        yield c
    pg_conn.rollback()


def _as_workflows(cur, sql, params=()):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(sql, params)
    value = cur.fetchone()[0]
    cur.execute("RESET ROLE")
    return value


REQUEST_SQL = (
    "SELECT request_scrna_cellranger_run(p_sample => %s, p_reference => 'tiny_ref', "
    "p_requested_by => %s, p_metadata => %s)"
)


def _request(cur, sample="tinygex", metadata=None):
    return _as_workflows(
        cur, REQUEST_SQL, (sample, USER, None if metadata is None else Jsonb(metadata))
    )


def _run(cur, run_id):
    cur.execute(f"SELECT params, metadata FROM {TABLE} WHERE id = %s", (run_id,))
    return cur.fetchone()


def _count(cur, table):
    cur.execute(f"SELECT count(*) FROM {table}")
    return cur.fetchone()[0]


def _refused(cur, sql, params, error):
    """`sql` fails with `error` and changes nothing; roles set before it stay set."""
    cur.execute("SAVEPOINT refused")
    try:
        with pytest.raises(error):
            cur.execute(sql, params)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT refused")


def _can_execute(cur, role, sig):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig))
    return cur.fetchone()[0]


def _signatures(cur):
    cur.execute(
        "SELECT oid::regprocedure::text FROM pg_proc "
        "WHERE proname = 'request_scrna_cellranger_run' ORDER BY 1"
    )
    return [r[0] for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


def test_the_details_are_stored_on_the_run_and_params_is_unchanged(cur):
    run_id = _request(cur, metadata=DETAILS)
    params, metadata = _run(cur, run_id)
    assert metadata == DETAILS
    assert params == {"sample": "tinygex", "reference": "tiny_ref"}


def test_a_run_with_details_is_queued_once(cur):
    run_id = _request(cur, metadata=DETAILS)
    cur.execute(f"SELECT message->>'run_id' FROM {QUEUE_TABLE}")
    assert [r[0] for r in cur.fetchall()] == [str(run_id)]


def test_the_old_three_argument_call_still_works_with_no_details(cur):
    run_id = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(%s, 'tiny_ref', %s)",
        ("tinygex", USER),
    )
    assert _run(cur, run_id)[1] is None


def test_an_empty_object_is_kept(cur):
    assert _run(cur, _request(cur, metadata={}))[1] == {}


def test_details_at_the_size_limit_are_accepted(cur):
    # {"notes": "..."} is 13 bytes around the value in jsonb's text form.
    at_limit = {"notes": "x" * (METADATA_MAX_BYTES - 13)}
    assert _run(cur, _request(cur, metadata=at_limit))[1] == at_limit


@pytest.mark.parametrize(
    "metadata",
    [[1, 2], "root", 5, True, {"notes": "x" * (METADATA_MAX_BYTES - 12)}],
    ids=["array", "string", "number", "boolean", "one-byte-over"],
)
def test_details_that_are_not_an_object_or_too_big_are_refused_and_nothing_queued(
    cur, metadata
):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    _refused(
        cur,
        REQUEST_SQL,
        ("tinygex", USER, Jsonb(metadata)),
        psycopg.errors.CheckViolation,
    )
    cur.execute("RESET ROLE")
    assert _count(cur, TABLE) == 0
    assert _count(cur, QUEUE_TABLE) == 0


def test_the_table_refuses_a_non_object_written_directly(cur):
    run_id = _request(cur)
    _refused(
        cur,
        f"UPDATE {TABLE} SET metadata = %s WHERE id = %s",
        (Jsonb([1]), run_id),
        psycopg.errors.CheckViolation,
    )


def test_the_name_rules_still_apply(cur):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    _refused(
        cur,
        REQUEST_SQL,
        ("S1.rep1", USER, Jsonb(DETAILS)),
        psycopg.errors.InvalidParameterValue,
    )
    cur.execute("RESET ROLE")
    assert _count(cur, TABLE) == 0


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


def test_only_the_new_signature_exists(cur):
    assert _signatures(cur) == [
        "request_scrna_cellranger_run(text,text,uuid,jsonb)"
    ]


@pytest.mark.parametrize(
    "role, allowed",
    [
        ("bloom_workflows", True),
        ("anon", False),
        ("authenticated", False),
        ("bloom_user", False),
        ("bloom_agent", False),
        ("bloom_writer", False),
    ],
)
def test_only_bloom_workflows_may_request_a_run(cur, role, allowed):
    assert _can_execute(cur, role, NEW_SIG) is allowed


def test_the_function_is_security_definer_with_a_fixed_search_path(cur):
    cur.execute(
        "SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (NEW_SIG,)
    )
    secdef, config = cur.fetchone()
    assert secdef is True
    assert config == ["search_path=pg_catalog, public, pgmq"]


# --------------------------------------------------------------------------- #
# Re-run and rollback
# --------------------------------------------------------------------------- #


def test_the_migration_can_be_run_again_without_losing_details(cur):
    run_id = _request(cur, metadata=DETAILS)
    cur.execute(_sql_body(MIGRATION))
    assert _run(cur, run_id)[1] == DETAILS
    assert _signatures(cur) == [
        "request_scrna_cellranger_run(text,text,uuid,jsonb)"
    ]
    assert _can_execute(cur, "bloom_workflows", NEW_SIG)


def test_the_rollback_restores_the_three_argument_function(cur):
    _request(cur, metadata=DETAILS)
    cur.execute(_sql_body(ROLLBACK))
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = 'metadata'",
        (TABLE,),
    )
    assert cur.fetchone() is None
    assert _signatures(cur) == ["request_scrna_cellranger_run(text,text,uuid)"]
    assert _can_execute(cur, "bloom_workflows", OLD_SIG)
    assert not _can_execute(cur, "authenticated", OLD_SIG)
    run_id = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(%s, 'tiny_ref', %s)",
        ("root_rep2", USER),
    )
    assert run_id is not None
