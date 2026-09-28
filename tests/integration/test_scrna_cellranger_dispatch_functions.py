"""
Integration tests for claim/complete/fail_scrna_cellranger_run: claiming and hiding a
message, stale and malformed messages, the redelivery limit, recording a submission or
a failure, EXECUTE grants, re-apply and rollback.

Each test applies the runs migration and this one inside its own transaction and rolls
it back, so the database is left unchanged.
"""

import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
RUNS_TABLE = "scrna_cellranger_runs"
QUEUE = "scrna_cellranger_dispatch"
USER = "00000000-0000-0000-0000-000000000001"
SIGNATURES = {
    "claim": "public.claim_scrna_cellranger_run(integer, integer)",
    "complete": "public.complete_scrna_cellranger_run(bigint, bigint, text)",
    "fail": "public.fail_scrna_cellranger_run(bigint, bigint, text)",
}


def _find_one(directory: str, glob: str) -> Path:
    matches = sorted((REPO_ROOT / "supabase" / directory).glob(glob))
    assert matches, f"no {glob} under supabase/{directory}"
    return matches[-1]


RUNS_MIGRATION = _find_one("migrations", "*_create_scrna_cellranger_runs.sql")
MIGRATION = _find_one("migrations", "*_add_scrna_cellranger_dispatch_functions.sql")
ROLLBACK = _find_one(
    "rollbacks", "*_add_scrna_cellranger_dispatch_functions_rollback.sql"
)


def _sql_body(path: Path) -> str:
    """The file without its BEGIN;/COMMIT; lines."""
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        c.execute(_sql_body(RUNS_MIGRATION))
        c.execute(_sql_body(MIGRATION))
        c.execute(f"DELETE FROM pgmq.q_{QUEUE}")
        yield c
    pg_conn.rollback()


def _as_workflows(cur, sql, params=()):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(sql, params)
    rows = cur.fetchall() if cur.description else None
    cur.execute("RESET ROLE")
    return rows


def _request(cur, sample="tinygex", reference="tiny_ref"):
    rows = _as_workflows(
        cur,
        "SELECT request_scrna_cellranger_run(%s, %s, %s)",
        (sample, reference, USER),
    )
    return rows[0][0]


def _claim(cur, vt=60, max_reads=5):
    return _as_workflows(
        cur, "SELECT * FROM claim_scrna_cellranger_run(%s, %s)", (vt, max_reads)
    )


def _run(cur, run_id):
    cur.execute(
        "SELECT status, message, argo_workflow_name, submitted_at IS NOT NULL, "
        f"completed_at IS NOT NULL FROM {RUNS_TABLE} WHERE id = %s",
        (run_id,),
    )
    return cur.fetchone()


def _count(cur, table):
    cur.execute(f"SELECT count(*) FROM pgmq.{table}_{QUEUE}")
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# claim
# --------------------------------------------------------------------------- #


def test_claim_on_an_empty_queue_returns_nothing(cur):
    assert _claim(cur) == []


def test_claim_returns_the_run_and_its_message(cur):
    run_id = _request(cur, sample="root_a", reference="tair10")
    (row,) = _claim(cur)
    assert row[:4] == (run_id, "root_a", "tair10", f"root_a__tair10__{USER}")
    assert isinstance(row[4], int)


def test_a_claimed_message_is_hidden_from_the_next_claim(cur):
    _request(cur)
    assert len(_claim(cur)) == 1
    assert _claim(cur) == []


def test_a_message_for_a_run_no_longer_queued_is_dropped(cur):
    run_id = _request(cur)
    cur.execute(f"UPDATE {RUNS_TABLE} SET status = 'running' WHERE id = %s", (run_id,))
    assert _claim(cur) == []
    assert _count(cur, "q") == 0
    assert _run(cur, run_id)[0] == "running"


def test_a_message_for_a_missing_run_is_dropped(cur):
    cur.execute("SELECT pgmq.send(%s, '{\"run_id\": 999999999}'::jsonb)", (QUEUE,))
    assert _claim(cur) == []
    assert _count(cur, "q") == 0


@pytest.mark.parametrize("message", ['{"x": 1}', '{"run_id": "abc"}', '{"run_id": -1}'])
def test_a_malformed_message_is_archived_without_raising(cur, message):
    cur.execute("SELECT pgmq.send(%s, %s::jsonb)", (QUEUE, message))
    assert _claim(cur) == []
    assert _count(cur, "q") == 0
    assert _count(cur, "a") == 1


def test_a_run_redelivered_past_the_limit_is_failed_and_archived(cur):
    run_id = _request(cur)
    # vt=0 makes the message visible again at once, as after a worker crash.
    assert len(_claim(cur, vt=0, max_reads=2)) == 1
    assert len(_claim(cur, vt=0, max_reads=2)) == 1
    assert _claim(cur, vt=0, max_reads=2) == []
    status, message, _, _, completed = _run(cur, run_id)
    assert (status, completed) == ("failed", True)
    assert message == "not submitted after 2 attempts"
    assert (_count(cur, "q"), _count(cur, "a")) == (0, 1)


# --------------------------------------------------------------------------- #
# complete
# --------------------------------------------------------------------------- #


def test_complete_records_the_workflow_and_deletes_the_message(cur):
    run_id = _request(cur)
    (row,) = _claim(cur)
    _as_workflows(
        cur,
        "SELECT complete_scrna_cellranger_run(%s, %s, %s)",
        (run_id, row[4], "scrna-cellranger-abc12"),
    )
    assert _run(cur, run_id) == (
        "submitted",
        None,
        "scrna-cellranger-abc12",
        True,
        False,
    )
    assert (_count(cur, "q"), _count(cur, "a")) == (0, 0)


def test_complete_does_not_overwrite_a_run_that_moved_on(cur):
    run_id = _request(cur)
    (row,) = _claim(cur)
    cur.execute(
        f"UPDATE {RUNS_TABLE} SET status = 'running', argo_workflow_name = 'wf-1' "
        "WHERE id = %s",
        (run_id,),
    )
    _as_workflows(
        cur,
        "SELECT complete_scrna_cellranger_run(%s, %s, %s)",
        (run_id, row[4], "wf-2"),
    )
    assert _run(cur, run_id)[:3] == ("running", None, "wf-1")


@pytest.mark.parametrize("name", [None, "", "   "])
def test_complete_refuses_an_empty_workflow_name(cur, name):
    run_id = _request(cur)
    (row,) = _claim(cur)
    cur.execute("SAVEPOINT bad_complete")
    cur.execute("SET LOCAL ROLE bloom_workflows")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        cur.execute(
            "SELECT complete_scrna_cellranger_run(%s, %s, %s)", (run_id, row[4], name)
        )
    cur.execute("ROLLBACK TO SAVEPOINT bad_complete")
    assert _run(cur, run_id)[0] == "queued"


# --------------------------------------------------------------------------- #
# fail
# --------------------------------------------------------------------------- #


def test_fail_marks_the_run_failed_and_archives_the_message(cur):
    run_id = _request(cur)
    (row,) = _claim(cur)
    _as_workflows(
        cur,
        "SELECT fail_scrna_cellranger_run(%s, %s, %s)",
        (run_id, row[4], "Argo Workflow submission failed"),
    )
    assert _run(cur, run_id) == (
        "failed",
        "Argo Workflow submission failed",
        None,
        False,
        True,
    )
    assert (_count(cur, "q"), _count(cur, "a")) == (0, 1)


def test_fail_does_not_overwrite_a_submitted_run(cur):
    run_id = _request(cur)
    (row,) = _claim(cur)
    _as_workflows(
        cur,
        "SELECT complete_scrna_cellranger_run(%s, %s, %s)",
        (run_id, row[4], "wf-1"),
    )
    _as_workflows(
        cur,
        "SELECT fail_scrna_cellranger_run(%s, %s, %s)",
        (run_id, row[4], "late failure"),
    )
    assert _run(cur, run_id)[:3] == ("submitted", None, "wf-1")


# --------------------------------------------------------------------------- #
# Grants and function settings
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fn", sorted(SIGNATURES))
@pytest.mark.parametrize(
    "role, allowed",
    [
        ("bloom_workflows", True),
        ("PUBLIC", False),
        ("anon", False),
        ("authenticated", False),
        ("bloom_user", False),
        ("bloom_agent", False),
    ],
)
def test_execute_is_granted_to_bloom_workflows_only(cur, fn, role, allowed):
    sig = SIGNATURES[fn]
    if role == "PUBLIC":
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_proc p, aclexplode(p.proacl) a "
            "WHERE p.oid = %s::regprocedure AND a.grantee = 0 "
            "AND a.privilege_type = 'EXECUTE')",
            (sig,),
        )
    else:
        cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig))
    assert cur.fetchone()[0] is allowed


@pytest.mark.parametrize("fn", sorted(SIGNATURES))
def test_functions_are_security_definer_with_pinned_search_path(cur, fn):
    cur.execute(
        "SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure",
        (SIGNATURES[fn],),
    )
    secdef, config = cur.fetchone()
    assert secdef is True
    assert any(c.startswith("search_path=") for c in config)


# --------------------------------------------------------------------------- #
# Re-apply and rollback
# --------------------------------------------------------------------------- #


def test_migration_body_is_idempotent(cur):
    cur.execute(_sql_body(MIGRATION))
    run_id = _request(cur)
    assert _claim(cur)[0][0] == run_id


def test_rollback_removes_the_functions_and_keeps_the_runs(cur):
    run_id = _request(cur)
    cur.execute(_sql_body(ROLLBACK))
    for sig in SIGNATURES.values():
        cur.execute("SELECT to_regprocedure(%s)", (sig,))
        assert cur.fetchone()[0] is None, f"rollback left {sig}"
    assert _run(cur, run_id)[0] == "queued"
    assert _count(cur, "q") == 1
