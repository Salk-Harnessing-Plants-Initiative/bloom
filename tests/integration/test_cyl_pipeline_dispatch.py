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
# 1.7 — claim/complete/fail wrapper functions (Phase 2 of bloom #11/#404: the
# dispatch worker that actually submits batches to Argo). These three functions
# extend the same cyl_pipeline_dispatch queue Phase 1's enqueue_cyl_pipeline_batch
# already writes to; see design.md for the run-completion aggregation rationale.
# --------------------------------------------------------------------------- #

CLAIM_FN = "claim_cyl_pipeline_batch"
COMPLETE_FN = "complete_cyl_pipeline_batch"
FAIL_FN = "fail_cyl_pipeline_batch"


def _enqueue(cur, run_id: int, batch_index: int, scan_ids: list[int]) -> int:
    """Enqueue as bloom_workflows, matching the real call path; returns msg_id."""
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(f"SELECT {ENQUEUE_FN}(%s, %s, %s)", (run_id, batch_index, scan_ids))
    msg_id = cur.fetchone()[0]
    cur.execute("RESET ROLE")
    return msg_id


def _seed_batch(cur, n_scans: int, batch_index: int = 0, run_id: int | None = None):
    """Seed n scans + their cyl_pipeline_run_scans rows for one batch under a
    (new, or given) run, enqueue that batch, and return (run_id, scan_ids, msg_id)."""
    if run_id is None:
        run_id = _seed_run(cur)
    scan_ids = [_seed_scan(cur) for _ in range(n_scans)]
    for sid in scan_ids:
        _seed_run_scan(cur, run_id, sid, batch_index=batch_index)
    msg_id = _enqueue(cur, run_id, batch_index, scan_ids)
    return run_id, scan_ids, msg_id


def _claim(cur, *, vt: int = 60, max_reads: int = 5):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(f"SELECT * FROM {CLAIM_FN}(%s, %s)", (vt, max_reads))
    row = cur.fetchone()
    cur.execute("RESET ROLE")
    return row  # (run_id, batch_index, scan_ids, msg_id) or None


def _complete(cur, run_id, batch_index, msg_id, scan_ids, workflow_name):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(
        f"SELECT {COMPLETE_FN}(%s, %s, %s, %s, %s)",
        (run_id, batch_index, msg_id, scan_ids, workflow_name),
    )
    cur.execute("RESET ROLE")


def _fail(cur, run_id, batch_index, msg_id, scan_ids, error):
    cur.execute("SET LOCAL ROLE bloom_workflows")
    cur.execute(
        f"SELECT {FAIL_FN}(%s, %s, %s, %s, %s)",
        (run_id, batch_index, msg_id, scan_ids, error),
    )
    cur.execute("RESET ROLE")


def _run_status(cur, run_id) -> str:
    cur.execute(f"SELECT status FROM {RUNS_TABLE} WHERE id = %s", (run_id,))
    return cur.fetchone()[0]


def _scan_rows(cur, run_id):
    cur.execute(
        f"SELECT scan_id, status, argo_workflow_name, attempts, error_message "
        f"FROM {SCANS_TABLE} WHERE run_id = %s ORDER BY scan_id",
        (run_id,),
    )
    return cur.fetchall()


def test_claim_returns_enqueued_batch_and_hides_it(pg_conn):
    with pg_conn.cursor() as cur:
        run_id, scan_ids, msg_id = _seed_batch(cur, 2, batch_index=3)
        claimed = _claim(cur)
        assert claimed == (run_id, 3, scan_ids, msg_id)
        # A second immediate claim is hidden by the first claim's visibility
        # timeout.
        assert _claim(cur) is None
    pg_conn.rollback()


def test_claim_on_empty_queue_returns_nothing(pg_conn):
    with pg_conn.cursor() as cur:
        assert _claim(cur) is None
    pg_conn.rollback()


def test_claim_redelivers_after_visibility_timeout_expires(pg_conn):
    with pg_conn.cursor() as cur:
        run_id, scan_ids, msg_id = _seed_batch(cur, 1, batch_index=0)
        first = _claim(cur, vt=60)
        assert first is not None
        # Force the visibility timeout to have already elapsed rather than
        # sleeping in a test — matches PR #469's technique for manipulating
        # pgmq's underlying message columns directly.
        cur.execute(
            f"UPDATE pgmq.q_{QUEUE} SET vt = now() - interval '1 second' "
            f"WHERE msg_id = %s",
            (msg_id,),
        )
        second = _claim(cur, vt=60)
        assert second == (run_id, 0, scan_ids, msg_id)
    pg_conn.rollback()


def test_claim_dead_letters_past_max_reads(pg_conn):
    with pg_conn.cursor() as cur:
        run_id, scan_ids, msg_id = _seed_batch(cur, 2, batch_index=0)
        cur.execute(
            f"UPDATE pgmq.q_{QUEUE} SET read_ct = 10 WHERE msg_id = %s", (msg_id,)
        )
        result = _claim(cur, max_reads=5)
        assert result is None

        rows = _scan_rows(cur, run_id)
        assert {r[1] for r in rows} == {"failed"}, "every scan in the batch is failed"

        cur.execute(
            f"SELECT count(*) FROM pgmq.a_{QUEUE} WHERE msg_id = %s", (msg_id,)
        )
        assert cur.fetchone()[0] == 1, "the message was archived, not merely hidden"
    pg_conn.rollback()


def test_claim_dead_letter_of_last_batch_settles_the_run(pg_conn):
    with pg_conn.cursor() as cur:
        run_id, scan_ids, msg_id = _seed_batch(cur, 1, batch_index=0)
        cur.execute(
            f"UPDATE pgmq.q_{QUEUE} SET read_ct = 10 WHERE msg_id = %s", (msg_id,)
        )
        assert _claim(cur, max_reads=5) is None
        assert _run_status(cur, run_id) == "failed", (
            "a run whose only/last batch is dead-lettered by claim itself "
            "must still settle — not stay stuck at queued/submitted forever"
        )
    pg_conn.rollback()


def test_complete_records_workflow_name_on_every_scan_in_batch(pg_conn):
    with pg_conn.cursor() as cur:
        run_id, scan_ids, msg_id = _seed_batch(cur, 3, batch_index=0)
        _complete(cur, run_id, 0, msg_id, scan_ids, "wf-abc123")

        rows = _scan_rows(cur, run_id)
        assert {r[2] for r in rows} == {"wf-abc123"}
        assert _claim(cur) is None, "the message was deleted, not just hidden"
    pg_conn.rollback()


def test_complete_marks_run_submitted_when_last_batch_settles(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur)
        _, scan_ids_a, msg_a = _seed_batch(cur, 1, batch_index=0, run_id=run_id)
        _, scan_ids_b, msg_b = _seed_batch(cur, 1, batch_index=1, run_id=run_id)

        _complete(cur, run_id, 0, msg_a, scan_ids_a, "wf-a")
        assert _run_status(cur, run_id) == "queued", "batch 1 is still outstanding"

        _complete(cur, run_id, 1, msg_b, scan_ids_b, "wf-b")
        assert _run_status(cur, run_id) == "submitted"
    pg_conn.rollback()


def test_concurrent_complete_of_last_two_batches_settles_run_exactly_once(
    pg_conn, pg_conninfo
):
    """Two INDEPENDENT connections complete a run's last two outstanding
    batches at once. The run-completion aggregation must settle to
    'submitted' exactly once — this is the actual proof for design.md's
    "aggregate inside the SQL function" decision; a sequential test cannot
    exercise the race it exists to prevent."""
    import threading

    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur)
        _, scan_ids_a, msg_a = _seed_batch(cur, 1, batch_index=0, run_id=run_id)
        _, scan_ids_b, msg_b = _seed_batch(cur, 1, batch_index=1, run_id=run_id)
    pg_conn.commit()  # visible to the two independent connections below

    results = {}
    barrier = threading.Barrier(2)

    def completer(key, batch_index, msg_id, scan_ids, name):
        with psycopg.connect(pg_conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SET ROLE bloom_workflows")
                barrier.wait()  # fire both completions as simultaneously as possible
                cur.execute(
                    f"SELECT {COMPLETE_FN}(%s, %s, %s, %s, %s)",
                    (run_id, batch_index, msg_id, scan_ids, name),
                )
                cur.execute("RESET ROLE")
                results[key] = True

    try:
        threads = [
            threading.Thread(
                target=completer, args=("a", 0, msg_a, scan_ids_a, "wf-a"), daemon=True
            ),
            threading.Thread(
                target=completer, args=("b", 1, msg_b, scan_ids_b, "wf-b"), daemon=True
            ),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert results == {"a": True, "b": True}

        with psycopg.connect(pg_conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT status FROM {RUNS_TABLE} WHERE id = %s", (run_id,))
                assert cur.fetchone()[0] == "submitted", (
                    "no lost update — the run settles to 'submitted' exactly once"
                )
    finally:
        # Rows (and any pgmq message a failed assertion above left unsettled)
        # are committed via independent connections, so clean up explicitly
        # — respecting FK order — rather than relying on rollback. Matches PR
        # #469's own cleanup for the same class of concurrency test.
        with psycopg.connect(pg_conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                for msg_id in (msg_a, msg_b):
                    cur.execute(
                        f"DELETE FROM pgmq.q_{QUEUE} WHERE msg_id = %s", (msg_id,)
                    )
                    cur.execute(
                        f"DELETE FROM pgmq.a_{QUEUE} WHERE msg_id = %s", (msg_id,)
                    )
                cur.execute(f"DELETE FROM {SCANS_TABLE} WHERE run_id = %s", (run_id,))
                cur.execute(
                    f"DELETE FROM cyl_scans WHERE id = ANY(%s)",
                    (scan_ids_a + scan_ids_b,),
                )
                cur.execute(f"DELETE FROM {RUNS_TABLE} WHERE id = %s", (run_id,))


def test_fail_marks_batch_scans_failed_and_dead_letters(pg_conn):
    with pg_conn.cursor() as cur:
        run_id, scan_ids, msg_id = _seed_batch(cur, 2, batch_index=0)
        _fail(cur, run_id, 0, msg_id, scan_ids, "boom")

        rows = _scan_rows(cur, run_id)
        assert {r[1] for r in rows} == {"failed"}
        assert {r[4] for r in rows} == {"boom"}

        cur.execute(
            f"SELECT count(*) FROM pgmq.a_{QUEUE} WHERE msg_id = %s", (msg_id,)
        )
        assert cur.fetchone()[0] == 1
        assert _claim(cur) is None, "archived, not redeliverable"
    pg_conn.rollback()


def test_fail_increments_attempts_on_every_scan_in_batch(pg_conn):
    with pg_conn.cursor() as cur:
        run_id, scan_ids, msg_id = _seed_batch(cur, 2, batch_index=0)
        _fail(cur, run_id, 0, msg_id, scan_ids, "boom")
        rows = _scan_rows(cur, run_id)
        assert {r[3] for r in rows} == {1}
    pg_conn.rollback()


def test_fail_does_not_clobber_a_completed_scan(pg_conn):
    """vt-expiry / deploy race: a batch is completed successfully, then a
    straggler second attempt reports failure for the same batch. The
    argo_workflow_name-guarded fail must NOT flip an already-submitted scan
    back to 'failed' — that would strand a real, running Workflow as if it
    never got submitted."""
    with pg_conn.cursor() as cur:
        run_id, scan_ids, msg_id = _seed_batch(cur, 1, batch_index=0)
        _complete(cur, run_id, 0, msg_id, scan_ids, "wf-real")
        _fail(cur, run_id, 0, msg_id, scan_ids, "late failure")

        rows = _scan_rows(cur, run_id)
        assert rows[0][1] == "queued"  # status untouched — never flipped to failed
        assert rows[0][2] == "wf-real"  # the real submission is preserved
    pg_conn.rollback()


def test_complete_is_idempotent_on_redelivery(pg_conn):
    with pg_conn.cursor() as cur:
        run_id, scan_ids, msg_id = _seed_batch(cur, 1, batch_index=0)
        _complete(cur, run_id, 0, msg_id, scan_ids, "wf-abc")
        _complete(cur, run_id, 0, msg_id, scan_ids, "wf-abc")  # must not raise

        rows = _scan_rows(cur, run_id)
        assert rows[0][2] == "wf-abc"
    pg_conn.rollback()


def test_run_marked_partial_when_batches_mixed(pg_conn):
    with pg_conn.cursor() as cur:
        run_id = _seed_run(cur)
        _, scan_ids_a, msg_a = _seed_batch(cur, 1, batch_index=0, run_id=run_id)
        _, scan_ids_b, msg_b = _seed_batch(cur, 1, batch_index=1, run_id=run_id)

        _complete(cur, run_id, 0, msg_a, scan_ids_a, "wf-a")
        _fail(cur, run_id, 1, msg_b, scan_ids_b, "boom")

        assert _run_status(cur, run_id) == "partial"
    pg_conn.rollback()


def test_run_marked_failed_when_all_batches_fail(pg_conn):
    with pg_conn.cursor() as cur:
        run_id, scan_ids, msg_id = _seed_batch(cur, 1, batch_index=0)
        _fail(cur, run_id, 0, msg_id, scan_ids, "boom")
        assert _run_status(cur, run_id) == "failed"
    pg_conn.rollback()


def test_wrappers_denied_to_public_and_session_roles(pg_conn):
    with pg_conn.cursor() as cur:
        sigs = [
            f"{CLAIM_FN}(integer, integer)",
            f"{COMPLETE_FN}(bigint, integer, bigint, bigint[], text)",
            f"{FAIL_FN}(bigint, integer, bigint, bigint[], text)",
        ]
        denied = (
            "anon",
            "authenticated",
            "public",
            "bloom_user",
            "bloom_writer",
            "bloom_admin",
        )
        for sig in sigs:
            for role in denied:
                cur.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig)
                )
                assert cur.fetchone()[0] is False, f"{role} must NOT execute {sig}"
            cur.execute(
                "SELECT has_function_privilege('bloom_workflows', %s, 'EXECUTE')",
                (sig,),
            )
            assert cur.fetchone()[0] is True, f"bloom_workflows must execute {sig}"
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 1.8 — Phase 2 migration idempotency + rollback fidelity
# --------------------------------------------------------------------------- #

_MIGRATION_2_GLOB = "*_add_cyl_pipeline_dispatch_functions.sql"
_ROLLBACK_2_GLOB = "*_add_cyl_pipeline_dispatch_functions_rollback.sql"
MIGRATION_2 = _find_one("migrations", _MIGRATION_2_GLOB)
ROLLBACK_2 = _find_one("rollbacks", _ROLLBACK_2_GLOB)


def test_phase2_migration_body_is_idempotent(pg_conn):
    if MIGRATION_2 is None:
        pytest.skip("Phase 2 migration not written yet")
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION_2))
        cur.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (CLAIM_FN,))
        assert cur.fetchone() is not None
    pg_conn.rollback()


def test_phase2_rollback_removes_new_functions_only(pg_conn):
    if MIGRATION_2 is None or ROLLBACK_2 is None:
        pytest.skip("Phase 2 migration/rollback not written yet")
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK_2))

        for fn in (CLAIM_FN, COMPLETE_FN, FAIL_FN):
            cur.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (fn,))
            assert cur.fetchone() is None, f"rollback did not drop {fn}"

        # Phase 1's own function/queue/table must be untouched.
        cur.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (ENQUEUE_FN,))
        assert cur.fetchone() is not None, "rollback must not drop enqueue_cyl_pipeline_batch"
        cur.execute(
            "SELECT queue_name FROM pgmq.list_queues() WHERE queue_name = %s", (QUEUE,)
        )
        assert cur.fetchone() is not None, "rollback must not drop the pgmq queue"
    pg_conn.rollback()  # restore the schema — leave the DB untouched


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
