"""
Integration tests for change `add-cyl-pipeline-trigger` (Phase 1 of bloom #11/#404).

`cyl_pipeline_runs` (one row per pipeline-trigger request) and `cyl_pipeline_run_scans`
(one row per scan in a run) are the durable record the `POST /workflows/pipeline` route
writes before enqueuing work. This phase adds no Argo/Kubernetes interaction — it only
enumerates, computes an informational dedup preview, writes these two tables, and
enqueues via a new pgmq queue (`cyl_pipeline_dispatch`) + a least-privilege
`enqueue_cyl_pipeline_batch` wrapper function.

These tests assert: table defaults/constraints; role-based RLS (`bloom_workflows` gets
SELECT+INSERT but NOT YET UPDATE — that grant is deferred to Phase 2); Realtime
publication membership + idempotent re-apply; the new `bloom_workflows` grant needed to
check ALL of a scan's trait sources (not just the most recent one — see design.md for
why `is_latest` is the wrong rule here); the enqueue wrapper's negative-authorization
guard; and an apply-then-rollback-then-verify test that actually exercises the
companion rollback script.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT as
`supabase_admin` (BYPASSRLS) and mutates nothing — every test rolls back. RLS is
exercised with `SET LOCAL ROLE`, matching `test_cyl_scan_intermediates.py` /
`test_cyl_writeback_rpc.py`.

Runs in CI's `compose-health-check` job after migrations are applied
(`uv run --extra test pytest tests/integration/ -v`).
"""

import re
from pathlib import Path

import pytest

# Skip the whole module if psycopg isn't available (matches the sibling tests).
psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
RUNS_TABLE = "cyl_pipeline_runs"
SCANS_TABLE = "cyl_pipeline_run_scans"
QUEUE = "cyl_pipeline_dispatch"
ENQUEUE_FN = "enqueue_cyl_pipeline_batch"

# The migration/rollback filenames are resolved at test-collection time (not hardcoded
# to one timestamp) so this file doesn't need editing if the timestamp changes.
_MIGRATION_GLOB = "*_create_cyl_pipeline_runs.sql"
_ROLLBACK_GLOB = "*_create_cyl_pipeline_runs_rollback.sql"


def _find_one(directory: str, glob: str) -> Path | None:
    matches = sorted((REPO_ROOT / "supabase" / directory).glob(glob))
    return matches[-1] if matches else None


def _sql_body(path: Path) -> str:
    """The migration/rollback body minus its BEGIN;/COMMIT; wrapper, applied inside
    the fixture's uncommitted transaction (CRLF-safe, matching the change-C/D pattern
    in test_cyl_scan_intermediates.py / test_cyl_writeback_rpc.py)."""
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


# --------------------------------------------------------------------------- #
# Seed helpers
# --------------------------------------------------------------------------- #


def _seed_scan(cur) -> int:
    cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
    return cur.fetchone()[0]


def _seed_run(cur, **overrides) -> int:
    fields = {
        "target_level": "experiment",
        "target_id": 1,
        "params": psycopg.types.json.Jsonb({}),
        "requested_by": "00000000-0000-0000-0000-000000000001",
        **overrides,
    }
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["%s"] * len(fields))
    cur.execute(
        f"INSERT INTO {RUNS_TABLE} ({cols}) VALUES ({placeholders}) RETURNING id",
        list(fields.values()),
    )
    return cur.fetchone()[0]


def _seed_run_scan(cur, run_id: int, scan_id: int, **overrides) -> int:
    fields = {"run_id": run_id, "scan_id": scan_id, **overrides}
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["%s"] * len(fields))
    cur.execute(
        f"INSERT INTO {SCANS_TABLE} ({cols}) VALUES ({placeholders}) RETURNING id",
        list(fields.values()),
    )
    return cur.fetchone()[0]


def _seed_trait_source(cur, scan_id: int, *, param_hash: str, name: str = "src") -> int:
    """A cyl_trait_sources row + a linking cyl_scan_traits row, mirroring the real
    write-back shape (metadata.params.param_hash) closely enough for the dedup
    preview's join to exercise against. `trait_id` is left NULL — this seed only
    needs the scan_id/source_id join path, not a real trait value."""
    metadata = psycopg.types.json.Jsonb({"params": {"param_hash": param_hash}})
    cur.execute(
        "INSERT INTO cyl_trait_sources (name, metadata) VALUES (%s, %s) RETURNING id",
        (name, metadata),
    )
    source_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_scan_traits (scan_id, source_id) VALUES (%s, %s)",
        (scan_id, source_id),
    )
    return source_id


# --------------------------------------------------------------------------- #
# 1.1 — cyl_pipeline_runs / cyl_pipeline_run_scans basic shape
# --------------------------------------------------------------------------- #


def test_cyl_pipeline_runs_defaults(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur)
        cur.execute(
            f"SELECT status, scan_count, done_count, reused_count, failed_count, "
            f"submitted_at, completed_at, error_message FROM {RUNS_TABLE} WHERE id = %s",
            (run_id,),
        )
        row = cur.fetchone()
        assert row == ("queued", 0, 0, 0, 0, None, None, None)
    pg_conn.rollback()


def test_cyl_pipeline_runs_scan_ids_target_id_nullable(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur, target_level="scan_ids", target_id=None)
        cur.execute(f"SELECT target_id FROM {RUNS_TABLE} WHERE id = %s", (run_id,))
        assert cur.fetchone()[0] is None
    pg_conn.rollback()


def test_cyl_pipeline_run_scans_unique_run_scan(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur)
        scan_id = _seed_scan(cur)
        _seed_run_scan(cur, run_id, scan_id)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _seed_run_scan(cur, run_id, scan_id)
    pg_conn.rollback()


def test_cyl_pipeline_run_scans_minimal_insert(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur)
        scan_id = _seed_scan(cur)
        rs_id = _seed_run_scan(cur, run_id, scan_id, status="queued")
        cur.execute(
            f"SELECT batch_index, argo_workflow_name, source_id FROM {SCANS_TABLE} "
            f"WHERE id = %s",
            (rs_id,),
        )
        assert cur.fetchone() == (None, None, None)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 1.2 — RLS enforcement
# --------------------------------------------------------------------------- #


def test_bloom_user_read_only(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur)  # as supabase_admin
        cur.execute("SET LOCAL ROLE bloom_user")
        cur.execute(f"SELECT count(*) FROM {RUNS_TABLE}")
        assert cur.fetchone()[0] is not None
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                f"UPDATE {RUNS_TABLE} SET status = 'failed' WHERE id = %s", (run_id,)
            )
    pg_conn.rollback()


def test_bloom_user_cannot_insert(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE bloom_user")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _seed_run(cur)
    pg_conn.rollback()


def test_bloom_workflows_can_insert_but_not_update(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE bloom_workflows")
        run_id = _seed_run(cur)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                f"UPDATE {RUNS_TABLE} SET status = 'failed' WHERE id = %s", (run_id,)
            )
    pg_conn.rollback()


def test_bloom_workflows_can_insert_only_populated_columns(pg_conn):
    """PR review finding: the original migration granted whole-table INSERT to
    bloom_workflows. pipeline.py only ever populates a known subset of columns —
    this proves the grant is now column-scoped to exactly that subset, matching
    the cyl_scan_videos precedent for the same role."""
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur)  # as supabase_admin
        scan_id = _seed_scan(cur)
        cur.execute("SET LOCAL ROLE bloom_workflows")
        cur.execute(
            f"INSERT INTO {SCANS_TABLE} (run_id, scan_id, batch_index, status) "
            f"VALUES (%s, %s, %s, %s)",
            (run_id, scan_id, 0, "queued"),
        )
    pg_conn.rollback()

    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur)
        scan_id = _seed_scan(cur)
        cur.execute("SET LOCAL ROLE bloom_workflows")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                f"INSERT INTO {SCANS_TABLE} "
                f"(run_id, scan_id, batch_index, status, argo_workflow_name) "
                f"VALUES (%s, %s, %s, %s, %s)",
                (run_id, scan_id, 0, "queued", "wf-forged"),
            )
    pg_conn.rollback()


def test_anon_denied(pg_conn):
    with pg_conn.cursor() as cur:
        _seed_run(
            cur
        )  # as supabase_admin, so anon has something it could (wrongly) see
        cur.execute("SET LOCAL ROLE anon")
        cur.execute(f"SELECT count(*) FROM {RUNS_TABLE}")
        assert cur.fetchone()[0] == 0
        cur.execute("RESET ROLE")
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 1.3 — Realtime publication
# --------------------------------------------------------------------------- #


def test_realtime_publication_includes_both_tables(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_publication_tables "
            "WHERE pubname = 'supabase_realtime' AND tablename IN (%s, %s)",
            (RUNS_TABLE, SCANS_TABLE),
        )
        found = {row[0] for row in cur.fetchall()}
    assert found == {RUNS_TABLE, SCANS_TABLE}
    pg_conn.rollback()


def test_realtime_publication_add_table_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        for table in (RUNS_TABLE, SCANS_TABLE):
            cur.execute(f"""
                DO $$
                BEGIN
                  ALTER PUBLICATION supabase_realtime ADD TABLE public.{table};
                EXCEPTION WHEN duplicate_object THEN
                  NULL;
                END$$;
                """)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 1.4 — bloom_workflows can check ALL of a scan's trait sources, not just latest
# --------------------------------------------------------------------------- #


def test_bloom_workflows_can_check_all_sources_for_a_scan(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id = _seed_scan(cur)
        _seed_trait_source(cur, scan_id, param_hash="hash-age-14", name="older")
        _seed_trait_source(cur, scan_id, param_hash="hash-age-21", name="newer")

        cur.execute("SET LOCAL ROLE bloom_workflows")
        cur.execute(
            "SELECT s.metadata->'params'->>'param_hash' "
            "FROM cyl_scan_traits t JOIN cyl_trait_sources s ON t.source_id = s.id "
            "WHERE t.scan_id = %s",
            (scan_id,),
        )
        hashes = {row[0] for row in cur.fetchall()}
    assert hashes == {
        "hash-age-14",
        "hash-age-21",
    }, "bloom_workflows must see every source for a scan, not only the highest source_id"
    pg_conn.rollback()


def test_bloom_workflows_can_check_wave_existence(pg_conn):
    # cyl_scans_extended inner-joins through wave/experiment, so a wave with zero
    # scans is indistinguishable from a nonexistent wave via that view alone — the
    # route needs a direct existence check against cyl_waves itself.
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO cyl_experiments (name) VALUES ('e') RETURNING id")
        experiment_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, 1) RETURNING id",
            (experiment_id,),
        )
        wave_id = cur.fetchone()[0]

        cur.execute("SET LOCAL ROLE bloom_workflows")
        cur.execute("SELECT id FROM cyl_waves WHERE id = %s", (wave_id,))
        assert cur.fetchone() is not None
        cur.execute("SELECT id FROM cyl_waves WHERE id = %s", (wave_id + 999_000,))
        assert cur.fetchone() is None
    pg_conn.rollback()


def test_bloom_workflows_can_check_experiment_existence(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO cyl_experiments (name) VALUES ('e2') RETURNING id")
        experiment_id = cur.fetchone()[0]

        cur.execute("SET LOCAL ROLE bloom_workflows")
        cur.execute("SELECT id FROM cyl_experiments WHERE id = %s", (experiment_id,))
        assert cur.fetchone() is not None
        cur.execute(
            "SELECT id FROM cyl_experiments WHERE id = %s", (experiment_id + 999_000,)
        )
        assert cur.fetchone() is None
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 1.5 — pgmq dispatch queue + least-privilege enqueue function
# --------------------------------------------------------------------------- #


def test_enqueue_creates_pgmq_message(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur)
        cur.execute("SET LOCAL ROLE bloom_workflows")
        cur.execute(
            f"SELECT {ENQUEUE_FN}(%s, %s, %s)",
            (run_id, 0, [1, 2, 3]),
        )
        msg_id = cur.fetchone()[0]
        assert msg_id is not None

        cur.execute("RESET ROLE")
        cur.execute(f"SELECT message FROM pgmq.read('{QUEUE}', 30, 10)")
        messages = [row[0] for row in cur.fetchall()]
    matching = [m for m in messages if m.get("run_id") == run_id]
    assert len(matching) == 1
    assert matching[0]["batch_index"] == 0
    assert matching[0]["scan_ids"] == [1, 2, 3]
    pg_conn.rollback()


def test_enqueue_execute_denied_to_anon_authenticated_public(pg_conn):
    with pg_conn.cursor() as cur:
        sig = f"{ENQUEUE_FN}(bigint, integer, bigint[])"
        cur.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (sig,))
        assert (
            cur.fetchone()[0] is False
        ), "PUBLIC must not execute the enqueue function"
        for role in ("anon", "authenticated"):
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig))
            assert cur.fetchone()[0] is False, f"{role} must not hold EXECUTE"
        cur.execute(
            "SELECT has_function_privilege('bloom_workflows', %s, 'EXECUTE')", (sig,)
        )
        assert cur.fetchone()[0] is True, "bloom_workflows should hold EXECUTE"
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 1.6 — Migration idempotency + rollback fidelity
# --------------------------------------------------------------------------- #

MIGRATION = _find_one("migrations", _MIGRATION_GLOB)
ROLLBACK = _find_one("rollbacks", _ROLLBACK_GLOB)


def test_migration_body_is_idempotent(pg_conn):
    if MIGRATION is None:
        pytest.skip("migration not written yet")
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (RUNS_TABLE,),
        )
        assert cur.fetchone() is not None
    pg_conn.rollback()


def test_rollback_removes_everything(pg_conn):
    if MIGRATION is None or ROLLBACK is None:
        pytest.skip("migration/rollback not written yet")
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK))

        for table in (RUNS_TABLE, SCANS_TABLE):
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            )
            assert cur.fetchone() is None, f"rollback did not drop {table}"

        cur.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (ENQUEUE_FN,))
        assert cur.fetchone() is None, "rollback did not drop the enqueue function"

        cur.execute(
            "SELECT queue_name FROM pgmq.list_queues() WHERE queue_name = %s", (QUEUE,)
        )
        assert cur.fetchone() is None, "rollback did not drop the pgmq queue"
    pg_conn.rollback()  # restore the schema — leave the DB untouched
