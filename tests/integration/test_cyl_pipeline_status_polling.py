"""
Integration tests for change `add-cyl-pipeline-status-polling` (Phase 3 of bloom
#11) — the `update_cyl_pipeline_run_status` `SECURITY DEFINER` wrapper function.

This is the last piece of `_settle_cyl_pipeline_run`'s status story: Phase 2's
`claim`/`complete`/`fail_cyl_pipeline_batch` only ever resolve a run to
`'submitted'`/`'failed'`/`'partial'` (dispatch outcome). This function is what a
new standalone poller (`services/workflows/status_poller.py`) calls after
observing a run's real Argo Workflow phase(s), to progress a run to `'running'`/
`'complete'` (or re-use `'failed'`/`'partial'` for a real pipeline outcome, not
just a dispatch one).

This is a distinct RPC/migration from Phase 2's pgmq dispatch functions — a new
file, not an extension of `test_cyl_pipeline_dispatch.py`.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT as
`supabase_admin` (BYPASSRLS) and mutates nothing — every test rolls back, except
the concurrency test, which commits its seed row and cleans it up explicitly
(see that test's own docstring).

Runs in CI's `compose-health-check` job after migrations are applied
(`uv run --extra test pytest tests/integration/ -v`).
"""

import re
import threading
from pathlib import Path

import pytest

# Skip the whole module if psycopg isn't available (matches the sibling tests).
psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
RUNS_TABLE = "cyl_pipeline_runs"
UPDATE_FN = "update_cyl_pipeline_run_status"

# The migration/rollback filenames are resolved at test-collection time (not
# hardcoded to one timestamp) so this file doesn't need editing if the
# timestamp changes.
_MIGRATION_GLOB = "*_add_cyl_pipeline_run_status_polling.sql"
_ROLLBACK_GLOB = "*_add_cyl_pipeline_run_status_polling_rollback.sql"


def _find_one(directory: str, glob: str) -> Path | None:
    matches = sorted((REPO_ROOT / "supabase" / directory).glob(glob))
    return matches[-1] if matches else None


def _sql_body(path: Path) -> str:
    """The migration/rollback body minus its BEGIN;/COMMIT; wrapper, applied
    inside the fixture's uncommitted transaction (CRLF-safe, matching the
    Phase 1/2 pattern in test_cyl_pipeline_dispatch.py)."""
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


# --------------------------------------------------------------------------- #
# Seed helpers
# --------------------------------------------------------------------------- #


def _seed_run(cur, **overrides) -> int:
    fields = {
        "target_level": "experiment",
        "target_id": 1,
        "params": psycopg.types.json.Jsonb({}),
        "requested_by": "00000000-0000-0000-0000-000000000001",
        "status": "submitted",
        **overrides,
    }
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["%s"] * len(fields))
    cur.execute(
        f"INSERT INTO {RUNS_TABLE} ({cols}) VALUES ({placeholders}) RETURNING id",
        list(fields.values()),
    )
    return cur.fetchone()[0]


def _run_row(cur, run_id: int):
    cur.execute(
        f"SELECT status, completed_at FROM {RUNS_TABLE} WHERE id = %s", (run_id,)
    )
    return cur.fetchone()


def _update_status(cur, run_id: int, status: str) -> None:
    cur.execute(f"SELECT {UPDATE_FN}(%s, %s)", (run_id, status))


# --------------------------------------------------------------------------- #
# 1.1 — update_cyl_pipeline_run_status behavior
# --------------------------------------------------------------------------- #


def test_update_marks_submitted_run_running(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur, status="submitted")
        _update_status(cur, run_id, "running")
        status, completed_at = _run_row(cur, run_id)
        assert status == "running"
        assert completed_at is None
    pg_conn.rollback()


def test_update_marks_running_run_complete_and_sets_completed_at(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur, status="running")
        _update_status(cur, run_id, "complete")
        status, completed_at = _run_row(cur, run_id)
        assert status == "complete"
        assert completed_at is not None
    pg_conn.rollback()


def test_update_overwrites_a_stale_dispatch_time_completed_at_with_real_completion_time(
    pg_conn,
):
    """Found during /review-pr: Phase 2's own _settle_cyl_pipeline_run stamps
    completed_at the moment dispatch merely settles to 'submitted' -- long
    before the pipeline itself actually finishes. update_cyl_pipeline_run_status
    must overwrite that stale value with the real completion time, not
    preserve it (the old 'never overwrite' behavior silently froze
    completed_at at dispatch time forever)."""
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur, status="running")
        cur.execute(
            f"UPDATE {RUNS_TABLE} SET completed_at = '2020-01-01T00:00:00+00' "
            f"WHERE id = %s",
            (run_id,),
        )
        _update_status(cur, run_id, "complete")
        _, completed_at = _run_row(cur, run_id)
        assert completed_at is not None
        assert completed_at.year > 2020, (
            "completed_at must be overwritten with the real completion time, "
            "not left frozen at the earlier (dispatch-time) stamp"
        )
    pg_conn.rollback()


def test_update_is_a_noop_on_a_run_already_terminal(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur, status="failed")
        _update_status(cur, run_id, "complete")
        status, completed_at = _run_row(cur, run_id)
        assert status == "failed"
        assert completed_at is None
    pg_conn.rollback()


def test_update_accepts_partial_as_a_source_state_and_advances_completed_at(pg_conn):
    """Found during /review-pr: Phase 2 can settle a run straight to 'partial'
    (some scans dispatch-failed, some succeeded) while it still has
    genuinely-dispatched batches whose real Argo outcome hasn't been checked.
    The poller must be able to re-examine such a run, so 'partial' is now an
    eligible source state (not just a terminal target) -- and each real
    reconfirmation advances completed_at, an accepted consequence of 'partial'
    runs remaining pollable (see design.md)."""
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur, status="partial")
        cur.execute(
            f"UPDATE {RUNS_TABLE} SET completed_at = '2020-01-01T00:00:00+00' "
            f"WHERE id = %s",
            (run_id,),
        )
        _update_status(cur, run_id, "partial")
        status, completed_at = _run_row(cur, run_id)
        assert status == "partial"
        assert completed_at is not None
        assert completed_at.year > 2020, (
            "a 'partial' run's completed_at must advance on each real "
            "reconfirmation, not stay frozen at the first dispatch-time stamp"
        )
    pg_conn.rollback()


def test_update_is_a_noop_on_a_run_still_queued(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur, status="queued")
        _update_status(cur, run_id, "running")
        status, _ = _run_row(cur, run_id)
        assert status == "queued"
    pg_conn.rollback()


def test_update_rejects_invalid_status_value(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur, status="submitted")
        with pytest.raises(psycopg.Error):
            _update_status(cur, run_id, "bogus")
    pg_conn.rollback()


def test_update_marks_run_partial(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur, status="submitted")
        _update_status(cur, run_id, "partial")
        status, completed_at = _run_row(cur, run_id)
        assert status == "partial"
        assert completed_at is not None
    pg_conn.rollback()


def test_update_marks_run_failed(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur, status="submitted")
        _update_status(cur, run_id, "failed")
        status, completed_at = _run_row(cur, run_id)
        assert status == "failed"
        assert completed_at is not None
    pg_conn.rollback()


def test_update_on_nonexistent_run_id_is_a_harmless_noop(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT max(id) FROM {RUNS_TABLE}")
        max_id = cur.fetchone()[0] or 0
        _update_status(cur, max_id + 1_000_000, "running")
    pg_conn.rollback()


def test_concurrent_update_calls_leave_the_run_in_a_valid_terminal_state(
    pg_conn, pg_conninfo
):
    """Two INDEPENDENT connections call update_cyl_pipeline_run_status for the
    same run at (as close to) the same instant — one with 'complete', one with
    'partial'. Mirrors
    test_concurrent_complete_of_last_two_batches_settles_run_exactly_once's
    exact technique from test_cyl_pipeline_dispatch.py: seed + commit via
    pg_conn (so the row is visible to both independent pg_conninfo
    connections), then race two threads. The guard `WHERE status IN
    ('submitted','running','partial')` means whichever call lands first wins
    and the second is a no-op — never a corrupted intermediate state. Because
    the seed
    row is committed outside the normal per-test rollback, it's cleaned up
    explicitly in a finally block."""
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur, status="submitted")
    pg_conn.commit()  # visible to the two independent connections below

    results = {}
    errors = {}
    barrier = threading.Barrier(2)

    def updater(key, status):
        try:
            with psycopg.connect(pg_conninfo, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET ROLE bloom_workflows")
                    barrier.wait()  # fire both calls as simultaneously as possible
                    cur.execute(f"SELECT {UPDATE_FN}(%s, %s)", (run_id, status))
                    cur.execute("RESET ROLE")
            results[key] = True
        except Exception as exc:  # pragma: no cover - failure path only
            errors[key] = exc

    try:
        threads = [
            threading.Thread(target=updater, args=("complete", "complete")),
            threading.Thread(target=updater, args=("partial", "partial")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors, f"concurrent update calls raised: {errors}"
        assert results == {"complete": True, "partial": True}

        with psycopg.connect(pg_conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT status FROM {RUNS_TABLE} WHERE id = %s", (run_id,)
                )
                final_status = cur.fetchone()[0]
        assert final_status in ("complete", "partial"), (
            f"run must end in exactly one valid terminal status, got {final_status!r}"
        )
    finally:
        with psycopg.connect(pg_conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {RUNS_TABLE} WHERE id = %s", (run_id,))


def test_wrapper_denied_to_public_and_session_roles(pg_conn):
    with pg_conn.cursor() as cur:
        sig = f"{UPDATE_FN}(bigint, text)"
        denied = (
            "anon",
            "authenticated",
            "public",
            "bloom_user",
            "bloom_writer",
            "bloom_admin",
        )
        for role in denied:
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig))
            assert cur.fetchone()[0] is False, f"{role} must NOT execute {sig}"
        cur.execute(
            "SELECT has_function_privilege('bloom_workflows', %s, 'EXECUTE')", (sig,)
        )
        assert cur.fetchone()[0] is True, "bloom_workflows must execute the wrapper"
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 1.2 — Migration idempotency + rollback fidelity
# --------------------------------------------------------------------------- #

MIGRATION = _find_one("migrations", _MIGRATION_GLOB)
ROLLBACK = _find_one("rollbacks", _ROLLBACK_GLOB)


def test_migration_body_is_idempotent(pg_conn):
    if MIGRATION is None:
        pytest.skip("migration not written yet")
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (UPDATE_FN,))
        assert cur.fetchone() is not None
    pg_conn.rollback()


def test_rollback_removes_new_function(pg_conn):
    if MIGRATION is None or ROLLBACK is None:
        pytest.skip("migration/rollback not written yet")
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK))
        cur.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (UPDATE_FN,))
        assert cur.fetchone() is None, "rollback did not drop update_cyl_pipeline_run_status"

        # Phase 1/2's own functions must be untouched.
        for fn in (
            "enqueue_cyl_pipeline_batch",
            "claim_cyl_pipeline_batch",
            "complete_cyl_pipeline_batch",
            "fail_cyl_pipeline_batch",
        ):
            cur.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (fn,))
            assert cur.fetchone() is not None, f"rollback must not drop {fn}"
    pg_conn.rollback()  # restore the schema — leave the DB untouched
