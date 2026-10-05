"""
Integration tests for change `fix-cyl-poller-unconcluded-runs` (bloom#1042): the
`cyl_pipeline_run_workflows` table, `record_cyl_pipeline_workflow_phase`, and
`close_cyl_pipeline_run_workflow_scans`.

The status poller stores each workflow's last terminal phase here so the outcome
survives Argo's ttlStrategy GC, and closes a run's leftover 'queued' rows through
the run-scoped close-out (a GC'd workflow's generated name can be reused by a
later run, so the name-only `fail_cyl_pipeline_run_scans_without_result` is not
safe for the poller).

LOCAL ONLY: `pg_conn` connects as `supabase_admin` (BYPASSRLS); every test rolls
back except the concurrency test, which commits its seed rows and deletes them in
a `finally`. Where the table is not yet in the database (a local DB that hasn't
run `make migrate-local`), the `schema` fixture applies the migration body inside
the test's own transaction, which the rollback then discards.

Runs in CI's `compose-health-check` job after migrations are applied.
"""

import re
import threading
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
TABLE = "cyl_pipeline_run_workflows"
RECORD_FN = "record_cyl_pipeline_workflow_phase"
CLOSE_FN = "close_cyl_pipeline_run_workflow_scans"
UPDATE_FN = "update_cyl_pipeline_run_status"


def _find_one(directory: str, glob: str) -> Path | None:
    matches = sorted((REPO_ROOT / "supabase" / directory).glob(glob))
    return matches[-1] if matches else None


MIGRATION = _find_one("migrations", "*_add_cyl_pipeline_run_workflows.sql")
ROLLBACK = _find_one("rollbacks", "*_add_cyl_pipeline_run_workflows_rollback.sql")


def _sql_body(path: Path) -> str:
    """The body minus its BEGIN;/COMMIT; wrapper and the trailing NOTIFY, so it
    runs inside the fixture's uncommitted transaction (CRLF-safe)."""
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
        and not re.match(r"^\s*NOTIFY\s+pgrst\b", line, re.IGNORECASE)
    )


def _table_exists(cur) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{TABLE}",))
    return cur.fetchone()[0]


@pytest.fixture
def schema(pg_conn):
    """The new objects, applied in this test's transaction when the database
    hasn't been migrated yet. With no migration file at all the tests fail on
    the missing objects — the expected red."""
    with pg_conn.cursor() as cur:
        if not _table_exists(cur) and MIGRATION is not None:
            cur.execute(_sql_body(MIGRATION))
    yield pg_conn
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Seed helpers (same shape as test_cyl_writeback_rpc.py's)
# --------------------------------------------------------------------------- #


def _seed_scan(cur) -> int:
    cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
    return cur.fetchone()[0]


def _seed_run(cur, status: str = "running") -> int:
    cur.execute(
        "INSERT INTO cyl_pipeline_runs (target_level, target_id, params, requested_by, status) "
        "VALUES ('scan_ids', NULL, '{}'::jsonb, '00000000-0000-0000-0000-000000000001', %s) "
        "RETURNING id",
        (status,),
    )
    return cur.fetchone()[0]


def _seed_row(cur, run_id: int, wf: str | None, status: str = "queued") -> int:
    scan_id = _seed_scan(cur)
    cur.execute(
        "INSERT INTO cyl_pipeline_run_scans (run_id, scan_id, argo_workflow_name, status) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (run_id, scan_id, wf, status),
    )
    return cur.fetchone()[0]


def _record(cur, run_id, wf, phase):
    cur.execute(f"SELECT {RECORD_FN}(%s, %s, %s)", (run_id, wf, phase))
    return cur.fetchone()[0]


def _close(cur, run_id, wf, message="msg"):
    cur.execute(f"SELECT {CLOSE_FN}(%s, %s, %s)", (run_id, wf, message))
    return cur.fetchone()[0]


def _stored(cur, run_id, wf):
    cur.execute(
        f"SELECT phase, observed_at FROM {TABLE} WHERE run_id = %s AND argo_workflow_name = %s",
        (run_id, wf),
    )
    return cur.fetchone()


def _row_state(cur, row_id):
    cur.execute(
        "SELECT status, error_message FROM cyl_pipeline_run_scans WHERE id = %s",
        (row_id,),
    )
    return cur.fetchone()


# --------------------------------------------------------------------------- #
# record_cyl_pipeline_workflow_phase
# --------------------------------------------------------------------------- #


def test_record_phase_inserts_a_first_terminal_phase(schema):
    with schema.cursor() as cur:
        run_id = _seed_run(cur)
        _seed_row(cur, run_id, "wf-a")
        assert _record(cur, run_id, "wf-a", "Succeeded") is True
        phase, observed_at = _stored(cur, run_id, "wf-a")
        assert phase == "Succeeded"
        assert observed_at is not None


def test_record_phase_same_phase_is_a_noop(schema):
    with schema.cursor() as cur:
        run_id = _seed_run(cur)
        _seed_row(cur, run_id, "wf-a")
        _record(cur, run_id, "wf-a", "Failed")
        cur.execute(
            f"UPDATE {TABLE} SET observed_at = '2020-01-01T00:00:00+00' WHERE run_id = %s",
            (run_id,),
        )
        assert _record(cur, run_id, "wf-a", "Failed") is False
        _phase, observed_at = _stored(cur, run_id, "wf-a")
        assert observed_at.year == 2020, "a repeated phase must not touch observed_at"


def test_record_phase_different_phase_replaces_and_advances_observed_at(schema):
    with schema.cursor() as cur:
        run_id = _seed_run(cur)
        _seed_row(cur, run_id, "wf-a")
        _record(cur, run_id, "wf-a", "Failed")
        cur.execute(
            f"UPDATE {TABLE} SET observed_at = '2020-01-01T00:00:00+00' WHERE run_id = %s",
            (run_id,),
        )
        assert _record(cur, run_id, "wf-a", "Succeeded") is True
        phase, observed_at = _stored(cur, run_id, "wf-a")
        assert phase == "Succeeded"
        assert observed_at.year > 2020


@pytest.mark.parametrize("owner", ["nobody", "other_run"])
def test_record_phase_unknown_workflow_for_run_writes_nothing(schema, owner):
    with schema.cursor() as cur:
        run_id = _seed_run(cur)
        _seed_row(cur, run_id, "wf-a")
        if owner == "other_run":
            other = _seed_run(cur)
            _seed_row(cur, other, "wf-b")
        assert _record(cur, run_id, "wf-b", "Succeeded") is False
        cur.execute(f"SELECT count(*) FROM {TABLE} WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize("phase", ["Running", "Pending", "", None])
def test_record_phase_rejects_non_terminal_phase(schema, phase):
    with schema.cursor() as cur:
        run_id = _seed_run(cur)
        _seed_row(cur, run_id, "wf-a")
        cur.execute("SAVEPOINT bad_phase")
        # The RPC's own guard, not the table's CHECK or NOT NULL, must refuse it.
        with pytest.raises(psycopg.errors.RaiseException, match="invalid p_phase"):
            _record(cur, run_id, "wf-a", phase)
        cur.execute("ROLLBACK TO SAVEPOINT bad_phase")
        cur.execute(f"SELECT count(*) FROM {TABLE} WHERE run_id = %s", (run_id,))
        assert cur.fetchone()[0] == 0


def test_record_phase_works_as_bloom_workflows_without_table_write_grant(schema):
    with schema.cursor() as cur:
        run_id = _seed_run(cur)
        _seed_row(cur, run_id, "wf-a")
        cur.execute("SET LOCAL ROLE bloom_workflows")
        assert _record(cur, run_id, "wf-a", "Error") is True
        cur.execute("RESET ROLE")
        assert _stored(cur, run_id, "wf-a")[0] == "Error"


def _cleanup(conninfo, run_ids):
    with psycopg.connect(conninfo, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{TABLE}",))
        if cur.fetchone()[0]:
            cur.execute(f"DELETE FROM {TABLE} WHERE run_id = ANY(%s)", (run_ids,))
        cur.execute(
            "SELECT array_agg(scan_id) FROM cyl_pipeline_run_scans WHERE run_id = ANY(%s)",
            (run_ids,),
        )
        scan_ids = cur.fetchone()[0] or []
        cur.execute(
            "DELETE FROM cyl_pipeline_run_scans WHERE run_id = ANY(%s)", (run_ids,)
        )
        cur.execute("DELETE FROM cyl_pipeline_runs WHERE id = ANY(%s)", (run_ids,))
        cur.execute("DELETE FROM cyl_scans WHERE id = ANY(%s)", (scan_ids,))


def test_concurrent_record_phase_same_key_does_not_raise(pg_conn, pg_conninfo):
    """Two independent connections record the same (run, workflow) at once:
    neither may hit a unique violation, and exactly one reports a write. Needs
    the migration applied to the database (`make migrate-local`), since the
    seed rows are committed."""
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur)
        _seed_row(cur, run_id, "wf-a")
    pg_conn.commit()

    errors = {}
    results = {}
    barrier = threading.Barrier(2, timeout=10)

    def recorder(key):
        try:
            with (
                psycopg.connect(pg_conninfo, autocommit=True) as conn,
                conn.cursor() as cur,
            ):
                cur.execute("SET ROLE bloom_workflows")
                barrier.wait()
                results[key] = _record(cur, run_id, "wf-a", "Succeeded")
        except Exception as exc:  # noqa: BLE001 - reported to the test
            errors[key] = exc

    try:
        threads = [
            threading.Thread(target=recorder, args=(k,), daemon=True)
            for k in ("a", "b")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        assert not any(t.is_alive() for t in threads), "a record call hung"
        assert not errors, f"concurrent record calls raised: {errors}"
        assert sorted(results.values()) == [False, True]
        with (
            psycopg.connect(pg_conninfo, autocommit=True) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(f"SELECT phase FROM {TABLE} WHERE run_id = %s", (run_id,))
            assert cur.fetchall() == [("Succeeded",)]
    finally:
        _cleanup(pg_conninfo, [run_id])


# --------------------------------------------------------------------------- #
# close_cyl_pipeline_run_workflow_scans
# --------------------------------------------------------------------------- #


def test_close_run_workflow_scans_is_scoped_to_its_run(schema):
    with schema.cursor() as cur:
        r1 = _seed_run(cur)
        r2 = _seed_run(cur)
        r1_queued = _seed_row(cur, r1, "wf-a")
        r1_written = _seed_row(cur, r1, "wf-a", status="written")
        r1_failed = _seed_row(cur, r1, "wf-a", status="failed")
        r2_queued = _seed_row(cur, r2, "wf-a")

        assert _close(cur, r1, "wf-a", "removed") == 1
        assert _row_state(cur, r1_queued) == ("failed", "removed")
        assert _row_state(cur, r2_queued) == ("queued", None)
        assert _row_state(cur, r1_written)[0] == "written"
        assert _row_state(cur, r1_failed) == ("failed", None)

        assert _close(cur, r1, "wf-a", "removed") == 0


def test_close_run_workflow_scans_works_as_bloom_workflows(schema):
    with schema.cursor() as cur:
        run_id = _seed_run(cur)
        row = _seed_row(cur, run_id, "wf-a")
        cur.execute("SET LOCAL ROLE bloom_workflows")
        assert _close(cur, run_id, "wf-a", "removed") == 1
        cur.execute("RESET ROLE")
        assert _row_state(cur, row) == ("failed", "removed")


@pytest.mark.parametrize("message", [None, "", "  "])
def test_close_run_workflow_scans_refuses_a_blank_message(schema, message):
    with schema.cursor() as cur:
        run_id = _seed_run(cur)
        row = _seed_row(cur, run_id, "wf-a")
        cur.execute("SAVEPOINT blank")
        with pytest.raises(psycopg.errors.RaiseException, match="p_error_message"):
            _close(cur, run_id, "wf-a", message)
        cur.execute("ROLLBACK TO SAVEPOINT blank")
        assert _row_state(cur, row) == ("queued", None)


# --------------------------------------------------------------------------- #
# Privileges, RLS and hardening
# --------------------------------------------------------------------------- #

FUNCTION_SIGS = (
    f"{RECORD_FN}(bigint, text, text)",
    f"{CLOSE_FN}(bigint, text, text)",
    f"{UPDATE_FN}(bigint, text, integer, integer)",
)


def test_run_workflows_privileges(schema):
    with schema.cursor() as cur:
        for sig in FUNCTION_SIGS:
            for role in (
                "anon",
                "authenticated",
                "public",
                "service_role",
                "bloom_user",
                "bloom_writer",
                "bloom_agent",
                "bloom_admin",
            ):
                cur.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig)
                )
                assert cur.fetchone()[0] is False, f"{role} must NOT execute {sig}"
            cur.execute(
                "SELECT has_function_privilege('bloom_workflows', %s, 'EXECUTE')",
                (sig,),
            )
            assert cur.fetchone()[0] is True, f"bloom_workflows must execute {sig}"

        for role in (
            "anon",
            "authenticated",
            "bloom_user",
            "bloom_writer",
            "bloom_agent",
            "bloom_workflows",
        ):
            for priv in ("INSERT", "UPDATE", "DELETE"):
                cur.execute(
                    "SELECT has_table_privilege(%s, %s, %s)",
                    (role, f"public.{TABLE}", priv),
                )
                assert (
                    cur.fetchone()[0] is False
                ), f"{role} must not hold {priv} on {TABLE}"
        cur.execute(
            "SELECT has_table_privilege('anon', %s, 'SELECT')", (f"public.{TABLE}",)
        )
        assert cur.fetchone()[0] is False, "anon must not read the table"
        cur.execute(
            "SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"public.{TABLE}",),
        )
        assert cur.fetchone()[0] is True, "RLS must be enabled"
        for priv in ("INSERT", "UPDATE", "DELETE"):
            cur.execute(
                "SELECT has_table_privilege('bloom_admin', %s, %s)",
                (f"public.{TABLE}", priv),
            )
            assert cur.fetchone()[0] is True, f"bloom_admin must hold {priv}"
        cur.execute(
            "SELECT 1 FROM pg_publication_tables "
            "WHERE pubname = 'supabase_realtime' AND schemaname = 'public' "
            "AND tablename = %s",
            (TABLE,),
        )
        assert cur.fetchone() is None, f"{TABLE} must not be Realtime-published"

        run_id = _seed_run(cur)
        _seed_row(cur, run_id, "wf-a")
        _record(cur, run_id, "wf-a", "Succeeded")
        for role in ("bloom_workflows", "bloom_user", "bloom_agent"):
            cur.execute(f"SET LOCAL ROLE {role}")
            cur.execute(f"SELECT count(*) FROM {TABLE} WHERE run_id = %s", (run_id,))
            assert (
                cur.fetchone()[0] == 1
            ), f"{role} must see the row through its SELECT policy"
            cur.execute("RESET ROLE")


@pytest.mark.parametrize(
    ("fn", "nargs"), [(RECORD_FN, 3), (CLOSE_FN, 3), (UPDATE_FN, 4)]
)
def test_run_workflow_functions_are_hardened(schema, fn, nargs):
    with schema.cursor() as cur:
        cur.execute(
            "SELECT prosecdef, proconfig, pg_get_userbyid(proowner) FROM pg_proc "
            "WHERE proname = %s AND pronargs = %s",
            (fn, nargs),
        )
        rows = cur.fetchall()
        assert len(rows) == 1, f"expected exactly one {fn}/{nargs}"
        secdef, proconfig, owner = rows[0]
        assert secdef is True
        assert "search_path=pg_catalog, public" in (proconfig or [])
        assert owner == "postgres"


# --------------------------------------------------------------------------- #
# Migration idempotency + rollback
# --------------------------------------------------------------------------- #


def test_run_workflows_migration_is_idempotent(pg_conn):
    assert MIGRATION is not None, "migration not written yet"
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute(_sql_body(MIGRATION))
        assert _table_exists(cur)
    pg_conn.rollback()


def test_run_workflows_rollback_restores_the_previous_status_rpc(pg_conn):
    assert (
        MIGRATION is not None and ROLLBACK is not None
    ), "migration/rollback not written yet"
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute(_sql_body(ROLLBACK))
        assert not _table_exists(cur), "rollback must drop the table"
        for fn in (RECORD_FN, CLOSE_FN):
            cur.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (fn,))
            assert cur.fetchone() is None, f"rollback must drop {fn}"
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'cyl_pipeline_runs' AND column_name = 'poller_concluded_at'"
        )
        assert cur.fetchone() is None, "rollback must drop poller_concluded_at"

        # The restored body re-accepts 'partial' as a source with no finality.
        run_id = _seed_run(cur, status="submitted")
        cur.execute(f"SELECT {UPDATE_FN}(%s, 'partial', NULL, NULL)", (run_id,))
        cur.execute(f"SELECT {UPDATE_FN}(%s, 'failed', NULL, NULL)", (run_id,))
        cur.execute("SELECT status FROM cyl_pipeline_runs WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] == "failed"
        cur.execute(
            "SELECT has_function_privilege('bloom_workflows', %s, 'EXECUTE')",
            (f"{UPDATE_FN}(bigint, text, integer, integer)",),
        )
        assert cur.fetchone()[0] is True
    pg_conn.rollback()
