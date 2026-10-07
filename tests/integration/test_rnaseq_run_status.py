"""
Integration tests for update_rnaseq_run_status, the status poller's call: runs move
forward only (submitted -> running -> succeeded / skipped / failed), a finished run
never changes, an identical report writes nothing, and only bloom_workflows may call it.

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

TABLE = "rnaseq_runs"
SIG = "public.update_rnaseq_run_status(bigint, text, text, jsonb, integer, text)"
SAMPLE_RULE_MIGRATION = _find_one("migrations", "*_limit_cellranger_sample_names.sql")
MIGRATION = _find_one("migrations", "*_add_rnaseq_run_status_function.sql")
ROLLBACK = _find_one("rollbacks", "*_add_rnaseq_run_status_function_rollback.sql")
PODS = {"stage": "wf-stage-sample-1", "qc": "wf-qc-2"}


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        _to_cellranger_schema(c)
        c.execute("DROP FUNCTION IF EXISTS " + SIG)
        c.execute(_sql_body(RNASEQ_RUNS_MIGRATION))
        c.execute(_sql_body(SAMPLE_RULE_MIGRATION))
        c.execute(_sql_body(MIGRATION))
        c.execute("DELETE FROM pgmq.q_rnaseq_dispatch")
        yield c
    pg_conn.rollback()


def _workflows(cur, sql, params=()):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(sql, params)
    rows = cur.fetchall() if cur.description else None
    cur.execute("RESET ROLE")
    return rows


def _submitted_run(cur, sample="tinygex"):
    """A run taken to 'submitted' through the real request, claim and complete calls."""
    run_id = _workflows(
        cur,
        "SELECT request_scrna_cellranger_run(%s, 'tiny_ref', %s)",
        (sample, USER),
    )[0][0]
    (row,) = _workflows(cur, "SELECT * FROM claim_rnaseq_run(60, 5)")
    _workflows(cur, "SELECT complete_rnaseq_run(%s, %s, 'wf-1')", (run_id, row[4]))
    return run_id


def _update(cur, run_id, status, step=None, pods=None, exit_code=None, message=None):
    rows = _workflows(
        cur,
        "SELECT update_rnaseq_run_status(%s, %s, %s, %s, %s, %s)",
        (
            run_id,
            status,
            step,
            psycopg.types.json.Jsonb(pods) if pods is not None else None,
            exit_code,
            message,
        ),
    )
    return rows[0][0]


def _run(cur, run_id):
    cur.execute(
        "SELECT status, current_step, step_pods, exit_code, message, "
        f"completed_at IS NOT NULL FROM {TABLE} WHERE id = %s",
        (run_id,),
    )
    return cur.fetchone()


def _refused(cur, params):
    cur.execute("SAVEPOINT refused")
    cur.execute("SET LOCAL ROLE bloom_workflows")
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            cur.execute(
                "SELECT update_rnaseq_run_status(%s, %s, %s, %s, %s, %s)", params
            )
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT refused")


# --------------------------------------------------------------------------- #
# Moving forward
# --------------------------------------------------------------------------- #


def test_a_submitted_run_becomes_running_with_its_step_and_pods(cur):
    run_id = _submitted_run(cur)
    assert _update(cur, run_id, "running", "qc", PODS) is True
    assert _run(cur, run_id) == ("running", "qc", PODS, None, None, False)


def test_a_running_run_moves_to_the_next_step(cur):
    run_id = _submitted_run(cur)
    _update(cur, run_id, "running", "qc", PODS)
    pods = {**PODS, "count": "wf-count-3"}
    assert _update(cur, run_id, "running", "count", pods) is True
    assert _run(cur, run_id)[:3] == ("running", "count", pods)


@pytest.mark.parametrize(
    "status, exit_code, message",
    [
        ("succeeded", 0, "Finished: loaded into Bloom"),
        ("skipped", 0, "Skipped"),
        ("failed", 3, "No Cell Ranger reference"),
    ],
)
def test_a_run_finishes_with_its_exit_code_message_and_time(
    cur, status, exit_code, message
):
    run_id = _submitted_run(cur)
    _update(cur, run_id, "running", "count", PODS)
    assert _update(cur, run_id, status, "cleanup", PODS, exit_code, message) is True
    assert _run(cur, run_id) == (status, "cleanup", PODS, exit_code, message, True)


def test_a_submitted_run_can_finish_without_being_seen_running(cur):
    run_id = _submitted_run(cur)
    assert _update(cur, run_id, "failed", "stage", None, 3, "No reference") is True
    assert _run(cur, run_id)[0] == "failed"


def test_the_reference_step_is_a_step_a_run_can_report(cur):
    run_id = _submitted_run(cur)
    assert _update(cur, run_id, "running", "stage-reference", PODS) is True
    assert _run(cur, run_id)[:2] == ("running", "stage-reference")


def test_a_new_pod_for_the_same_step_is_recorded(cur):
    # Argo retrying a step starts a new pod while the step stays the same.
    run_id = _submitted_run(cur)
    _update(cur, run_id, "running", "qc", PODS)
    retried = {**PODS, "qc": "wf-qc-retry-9"}
    assert _update(cur, run_id, "running", "qc", retried) is True
    assert _run(cur, run_id)[2] == retried


def test_the_same_pods_in_another_key_order_write_nothing(cur):
    run_id = _submitted_run(cur)
    _update(cur, run_id, "running", "qc", {"stage": "a", "qc": "b"})
    assert _update(cur, run_id, "running", "qc", {"qc": "b", "stage": "a"}) is False


def test_a_null_step_or_pods_keeps_the_stored_values(cur):
    run_id = _submitted_run(cur)
    _update(cur, run_id, "running", "qc", PODS)
    _update(cur, run_id, "failed", None, None, 5, "Cell Ranger failed")
    assert _run(cur, run_id)[1:3] == ("qc", PODS)


def test_running_does_not_set_an_exit_code_message_or_end_time(cur):
    run_id = _submitted_run(cur)
    _update(cur, run_id, "running", "qc", PODS, 7, "ignored")
    assert _run(cur, run_id)[3:] == (None, None, False)


# --------------------------------------------------------------------------- #
# Never moving back
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("finished", ["succeeded", "skipped", "failed"])
@pytest.mark.parametrize("later", ["running", "succeeded", "failed"])
def test_a_finished_run_never_changes(cur, finished, later):
    run_id = _submitted_run(cur)
    _update(cur, run_id, finished, "cleanup", PODS, 0, "done")
    before = _run(cur, run_id)
    assert _update(cur, run_id, later, "stage", {"x": "y"}, 9, "late report") is False
    assert _run(cur, run_id) == before


def test_a_queued_run_is_not_touched(cur):
    run_id = _workflows(
        cur, "SELECT request_scrna_cellranger_run('tinygex', 'tiny_ref', %s)", (USER,)
    )[0][0]
    assert _update(cur, run_id, "running", "stage", PODS) is False
    assert _run(cur, run_id)[0] == "queued"


def test_an_unknown_run_changes_nothing(cur):
    assert _update(cur, 999999999, "running", "stage", PODS) is False


# --------------------------------------------------------------------------- #
# Identical reports
# --------------------------------------------------------------------------- #


def test_an_identical_report_writes_nothing(cur):
    run_id = _submitted_run(cur)
    _update(cur, run_id, "running", "qc", PODS)
    cur.execute(f"SELECT updated_at FROM {TABLE} WHERE id = %s", (run_id,))
    before = cur.fetchone()[0]
    assert _update(cur, run_id, "running", "qc", PODS) is False
    cur.execute(f"SELECT updated_at FROM {TABLE} WHERE id = %s", (run_id,))
    assert cur.fetchone()[0] == before


def test_a_report_with_only_the_same_status_writes_nothing(cur):
    run_id = _submitted_run(cur)
    _update(cur, run_id, "running", "qc", PODS)
    assert _update(cur, run_id, "running", None, None) is False


# --------------------------------------------------------------------------- #
# Refused input
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "params",
    [
        (None, "running", None, None, None, None),
        (1, None, None, None, None, None),
        (1, "queued", None, None, None, None),
        (1, "submitted", None, None, None, None),
        (1, "complete", None, None, None, None),
        (1, "failed", None, None, 3, None),
        (1, "failed", None, None, 3, "  "),
        (1, "running", None, psycopg.types.json.Jsonb(["a"]), None, None),
    ],
)
def test_bad_input_is_refused(cur, params):
    _refused(cur, params)


def test_a_step_cellranger_does_not_have_is_refused_by_the_table(cur):
    run_id = _submitted_run(cur)
    cur.execute("SAVEPOINT step")
    with pytest.raises(psycopg.errors.CheckViolation):
        _update(cur, run_id, "running", "align", PODS)
    cur.execute("ROLLBACK TO SAVEPOINT step")
    assert _run(cur, run_id)[0] == "submitted"


# --------------------------------------------------------------------------- #
# Grants, settings, re-apply and rollback
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "role, allowed",
    [
        ("bloom_workflows", True),
        ("public", False),
        ("anon", False),
        ("authenticated", False),
        ("bloom_user", False),
        ("bloom_agent", False),
    ],
)
def test_execute_is_granted_to_bloom_workflows_only(cur, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, SIG))
    assert cur.fetchone()[0] is allowed


def test_the_function_is_security_definer_with_a_pinned_search_path(cur):
    cur.execute(
        "SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (SIG,)
    )
    secdef, config = cur.fetchone()
    assert secdef is True
    assert "search_path=pg_catalog, public" in config


def test_reapplying_the_migration_keeps_runs_and_grants(cur):
    run_id = _submitted_run(cur)
    _update(cur, run_id, "running", "qc", PODS)
    cur.execute(_sql_body(MIGRATION))
    assert _run(cur, run_id)[0] == "running"
    cur.execute("SELECT has_function_privilege('anon', %s, 'EXECUTE')", (SIG,))
    assert cur.fetchone()[0] is False


def test_the_rollback_stops_while_a_run_reports_the_reference_step(cur):
    run_id = _submitted_run(cur)
    _update(cur, run_id, "running", "stage-reference", PODS)
    cur.execute("SAVEPOINT rollback_blocked")
    with pytest.raises(psycopg.errors.RaiseException):
        cur.execute(_sql_body(ROLLBACK))
    cur.execute("ROLLBACK TO SAVEPOINT rollback_blocked")
    cur.execute("SELECT to_regprocedure(%s)", (SIG,))
    assert cur.fetchone()[0] is not None


def test_the_rollback_drops_the_function_and_keeps_the_runs(cur):
    run_id = _submitted_run(cur)
    _update(cur, run_id, "running", "qc", PODS)
    cur.execute(_sql_body(ROLLBACK))
    cur.execute("SELECT to_regprocedure(%s)", (SIG,))
    assert cur.fetchone()[0] is None
    assert _run(cur, run_id)[0] == "running"
