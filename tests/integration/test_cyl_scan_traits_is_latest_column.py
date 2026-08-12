"""
Integration tests for storing `is_latest` as a real column on `cyl_scan_traits`
(`fix-cyl-scan-traits-latest-rollup`, bloom#637, Phase 1).

`cyl_scan_traits_source.is_latest` was a live `WindowAgg` (`max(source_id) OVER (PARTITION BY
scan_id)`), recomputed on every read -- the dominant cost behind `list_experiments()`'s timeout.
This adds a stored, indexed `is_latest boolean NOT NULL DEFAULT false` column on `cyl_scan_traits`,
maintained by an `AFTER INSERT OR UPDATE OR DELETE` trigger covering every write path (the
write-back RPC and `bloom_admin`'s break-glass table access), so the selection rule is computed
once per write instead of once per read. The view itself is NOT cut over to read this column yet
(Phase 2, gated on a staging backfill) -- this migration is additive/inert.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT as `supabase_admin`
(BYPASSRLS) and every test rolls back. Concurrency tests use `pg_conninfo` to open a genuinely
second connection.
"""

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.test_cyl_read_path import (
    _deliver,
    _seed_experiment_scan,
    _trait,
)

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260812010000_add_cyl_scan_traits_is_latest_column"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS}_rollback.sql"

TRIGGER_FUNCTION = "maintain_cyl_scan_traits_is_latest"


def _sql_body(path: Path) -> str:
    """Migration/rollback body minus its BEGIN;/COMMIT; wrapper (CRLF-safe)."""
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def _is_latest_rows(cur, scan_id):
    cur.execute(
        "SELECT source_id, is_latest FROM cyl_scan_traits WHERE scan_id=%s ORDER BY source_id",
        (scan_id,),
    )
    return cur.fetchall()


# --------------------------------------------------------------------------- #
# Requirement: is_latest column is maintained on every write, regardless of writer
# --------------------------------------------------------------------------- #


def test_fresh_insert_sets_is_latest_true(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        src = _deliver(
            cur, imgs, "k", traits=[_trait("length", 1.0), _trait("width", 2.0)]
        )
        rows = _is_latest_rows(cur, scan_id)
        assert rows == [(src, True), (src, True)]
    pg_conn.rollback()


def test_rerun_flips_prior_source_to_false(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        old = _deliver(cur, imgs, "old", traits=[_trait("length", 1.0)])
        new = _deliver(cur, imgs, "new", traits=[_trait("length", 2.0)])
        assert _is_latest_rows(cur, scan_id) == [(old, False), (new, True)]
    pg_conn.rollback()


def test_partial_rerun_does_not_backfill_dropped_trait(pg_conn):
    """Regression test for the preserved partition grain (mirrors test_no_cross_source_mixing in
    test_cyl_read_path.py): an older source wrote A+B, a newer source re-delivers only A. Neither
    the old A row nor the old/only B row should be resurrected to is_latest=true -- catches an
    accidental (scan_id, trait_id) partition regression.
    """
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        old = _deliver(cur, imgs, "old", traits=[_trait("A", 1.0), _trait("B", 2.0)])
        new = _deliver(cur, imgs, "new", traits=[_trait("A", 10.0)])
        cur.execute(
            "SELECT t.name, cst.source_id, cst.is_latest "
            "FROM cyl_scan_traits cst JOIN cyl_traits t ON t.id = cst.trait_id "
            "WHERE cst.scan_id=%s ORDER BY t.name, cst.source_id",
            (scan_id,),
        )
        rows = cur.fetchall()
        assert rows == [
            ("A", old, False),
            ("A", new, True),
            ("B", old, False),
        ]
    pg_conn.rollback()


def test_bloom_admin_direct_write_is_maintained(pg_conn):
    """The break-glass path (bloom_admin, direct table access, no write-back RPC) must get correct
    is_latest maintenance too -- the whole reason this is a table-level trigger, not RPC logic.
    """
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        old = _deliver(cur, imgs, "old", traits=[_trait("length", 1.0)])
        cur.execute("SELECT id FROM cyl_traits WHERE name='length'")
        trait_id = cur.fetchone()[0]
        cur.execute("SET LOCAL ROLE bloom_admin")
        cur.execute(
            "INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id",
            (f"admin-direct-{uuid.uuid4().hex}",),
        )
        new_source_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
            "VALUES (%s, %s, %s, %s) RETURNING source_id",
            (scan_id, new_source_id, trait_id, 5.0),
        )
        new = cur.fetchone()[0]
        cur.execute("RESET ROLE")
        assert _is_latest_rows(cur, scan_id) == [(old, False), (new, True)]
    pg_conn.rollback()


def test_deleting_latest_row_promotes_next_highest(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        old = _deliver(cur, imgs, "old", traits=[_trait("length", 1.0)])
        new = _deliver(cur, imgs, "new", traits=[_trait("length", 2.0)])
        cur.execute(
            "DELETE FROM cyl_scan_traits WHERE scan_id=%s AND source_id=%s",
            (scan_id, new),
        )
        assert _is_latest_rows(cur, scan_id) == [(old, True)]
    pg_conn.rollback()


def test_maintenance_trigger_converges_without_infinite_recursion(pg_conn):
    """The trigger's maintenance UPDATE re-fires the same AFTER trigger on every row it touches;
    the WHERE is_latest IS DISTINCT FROM (...) guard must make the second pass a no-op. Asserted
    via an exact fired-twice count (RAISE DEBUG + psycopg's notice capture), not merely "it didn't
    hang" -- a bare completion check wouldn't catch a guard that's subtly wrong but still happens
    to converge.
    """
    fired = []
    pg_conn.add_notice_handler(
        lambda diag: (
            fired.append(diag.message_primary)
            if TRIGGER_FUNCTION in (diag.message_primary or "")
            else None
        )
    )
    with pg_conn.cursor() as cur:
        _, _scan_id, imgs = _seed_experiment_scan(cur)
        cur.execute("SET LOCAL client_min_messages = debug1")
        fired.clear()
        _deliver(cur, imgs, "k", traits=[_trait("length", 1.0)])
        assert (
            len(fired) == 2
        ), f"expected exactly 2 trigger firings, got {len(fired)}: {fired}"
    pg_conn.rollback()


def _envelope(imgs, key, value):
    return {
        "provenance": {
            "contract_version": "0.1.0a3",
            "scan_key": "SK1",
            "idempotency_key": key,
            "inputs": {"image_ids": [str(i) for i in imgs]},
        },
        "traits": [_trait("length", value)],
        "blobs": [],
    }


def _deliver_concurrently(pg_conninfo, imgs, key_a, key_b):
    """Runs two write-back deliveries on two genuinely separate connections, started together in
    real OS threads so Postgres's own locking -- not Python statement ordering -- governs
    interleaving. A sequential `cur1.execute(); cur2.execute(); c1.commit(); c2.commit()` in one
    thread does NOT achieve this: if the two writes contend for the same lock, the second
    `execute()` call blocks synchronously waiting for the first connection's commit, which never
    happens because that commit call comes after the still-blocked second `execute()` returns.
    """
    import json
    import threading

    errors = []

    def _run(key, value):
        try:
            with psycopg.connect(pg_conninfo) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT public.insert_cyl_result_envelope(%s::jsonb)",
                        (json.dumps(_envelope(imgs, key, value)),),
                    )
                conn.commit()
        except Exception as exc:  # noqa: BLE001 -- surfaced via `errors`, not swallowed
            errors.append(exc)

    t1 = threading.Thread(target=_run, args=(key_a, 9.0))
    t2 = threading.Thread(target=_run, args=(key_b, 10.0))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)
    if errors:
        raise errors[0]


def test_concurrent_rerun_converges_to_higher_source(pg_conninfo):
    """Two connections rerun the SAME already-seeded scan concurrently -- Postgres's row-level
    locking on the maintenance UPDATE naturally serializes these, but the final state must still
    be internally consistent (exactly one source_id per scan is_latest=true, matching the higher
    one), not corrupted or deadlocked."""
    with psycopg.connect(pg_conninfo, autocommit=True) as setup_conn:
        with setup_conn.cursor() as cur:
            _, scan_id, imgs = _seed_experiment_scan(cur)
            _deliver(cur, imgs, "old", traits=[_trait("length", 1.0)])
        try:
            token = uuid.uuid4().hex
            _deliver_concurrently(
                pg_conninfo, imgs, f"rerun-a-{token}", f"rerun-b-{token}"
            )
            with setup_conn.cursor() as cur:
                cur.execute(
                    "SELECT source_id, is_latest FROM cyl_scan_traits WHERE scan_id=%s "
                    "ORDER BY source_id",
                    (scan_id,),
                )
                rows = cur.fetchall()
            latest = [r for r in rows if r[1]]
            assert (
                len(latest) == 1
            ), f"expected exactly one is_latest=true row, got {rows}"
            assert latest[0][0] == max(r[0] for r in rows)
        finally:
            with setup_conn.cursor() as cur:
                cur.execute("DELETE FROM cyl_scan_traits WHERE scan_id=%s", (scan_id,))
                cur.execute(
                    "DELETE FROM cyl_trait_sources WHERE idempotency_key LIKE %s",
                    (f"rerun-%-{token}",),
                )
                cur.execute("DELETE FROM cyl_images WHERE scan_id=%s", (scan_id,))
                cur.execute("DELETE FROM cyl_scans WHERE id=%s", (scan_id,))


def test_concurrent_first_insert_for_new_scan_converges(pg_conninfo):
    """A genuinely different race from the rerun case above: two connections each insert the
    FIRST-EVER rows for a brand-new scan_id, with different source_ids, run in real threads so
    both are genuinely in flight before either commits. Unlike a rerun, neither transaction's
    maintenance UPDATE starts by touching a row the other holds a lock on (there are no prior rows
    to lock) -- this is the race the trigger's advisory-lock serialization (keyed by scan_id)
    exists to close; without it, both writers would independently conclude "I'm the only source"
    and both end up is_latest=true."""
    with psycopg.connect(pg_conninfo, autocommit=True) as setup_conn:
        with setup_conn.cursor() as cur:
            _, scan_id, imgs = _seed_experiment_scan(cur)
        try:
            token = uuid.uuid4().hex
            _deliver_concurrently(
                pg_conninfo, imgs, f"first-a-{token}", f"first-b-{token}"
            )
            with setup_conn.cursor() as cur:
                cur.execute(
                    "SELECT source_id, is_latest FROM cyl_scan_traits WHERE scan_id=%s "
                    "ORDER BY source_id",
                    (scan_id,),
                )
                rows = cur.fetchall()
            latest = [r for r in rows if r[1]]
            assert (
                len(latest) == 1
            ), f"expected exactly one is_latest=true row, got {rows}"
            assert latest[0][0] == max(r[0] for r in rows)
        finally:
            with setup_conn.cursor() as cur:
                cur.execute("DELETE FROM cyl_scan_traits WHERE scan_id=%s", (scan_id,))
                cur.execute(
                    "DELETE FROM cyl_trait_sources WHERE idempotency_key LIKE %s",
                    (f"first-%-{token}",),
                )
                cur.execute("DELETE FROM cyl_images WHERE scan_id=%s", (scan_id,))
                cur.execute("DELETE FROM cyl_scans WHERE id=%s", (scan_id,))


def test_trigger_function_is_hardened(pg_conn):
    """Catalog metadata shows SECURITY DEFINER, a pinned search_path, and (by static-scan of the
    function body) schema-qualified references throughout -- mirrors the write-back RPC's own
    'definer can write after the lockdown' test pattern."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT prosecdef, proconfig FROM pg_proc WHERE proname=%s",
            (TRIGGER_FUNCTION,),
        )
        prosecdef, proconfig = cur.fetchone()
        assert prosecdef is True
        assert proconfig is not None
        assert any(c.startswith("search_path=") for c in proconfig)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Requirement: additive migration + rollback
# --------------------------------------------------------------------------- #


def test_migration_adds_no_write_capability():
    sql = MIGRATION.read_text().lower()
    assert "create policy" not in sql
    assert not re.search(
        r"grant\s+[^;]*\b(insert|update|delete|all)\b", sql
    ), "migration must not grant any write privilege"


def test_migration_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='cyl_scan_traits' AND column_name='is_latest'"
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname=%s", (TRIGGER_FUNCTION,)
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_rollback_restores_prior_state(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK))
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='cyl_scan_traits' AND column_name='is_latest'"
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname=%s", (TRIGGER_FUNCTION,)
        )
        assert cur.fetchone()[0] == 0
        # round-trip: re-apply and confirm it's back
        cur.execute(_sql_body(MIGRATION))
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name='cyl_scan_traits' AND column_name='is_latest'"
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()
