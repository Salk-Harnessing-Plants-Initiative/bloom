"""
Integration tests for `cyl_experiment_trait_counts` (bloom#637 / bloom#656).

Caches `n_traits` per experiment (distinct latest-source trait ids among plants with a non-null
accession), refreshed by `refresh_cyl_experiment_trait_counts()` on an external schedule -- not a
per-write trigger, since one write-back upload inserts hundreds of trait rows in a loop and a
per-row trigger would fire that many full-experiment recomputes for one upload (design.md D5).

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT as `supabase_admin`
(BYPASSRLS); every test rolls back.
"""

import re
import threading
import time
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.test_cyl_read_path import (  # noqa: E402
    _deliver,
    _seed_experiment_scan,
    _trait,
)
from tests.integration.test_cyl_scan_latest_source import (  # noqa: E402
    _cleanup_seeded_experiment,
)

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260817140000_create_cyl_experiment_trait_counts"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS}_rollback.sql"

# The later migration in this change, whose rollback must run BEFORE this one's -- see the
# rollback SQL files' own "ROLLBACK ORDER" header comments.
_REWRITE_TS = "20260817150000_rewrite_get_experiment_summary_counts"
REWRITE_MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_REWRITE_TS}.sql"
REWRITE_ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_REWRITE_TS}_rollback.sql"


def _sql_body(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def _refresh(cur):
    cur.execute("SELECT public.refresh_cyl_experiment_trait_counts()")


def _n_traits(cur, experiment_id):
    cur.execute(
        "SELECT n_traits FROM cyl_experiment_trait_counts WHERE experiment_id=%s",
        (experiment_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _live_n_traits(cur, experiment_id):
    """Hand-computed oracle, independent of the cache: distinct latest-source trait ids for
    plants with a non-null accession, for one experiment."""
    cur.execute(
        """
        SELECT count(DISTINCT cst.trait_id)
        FROM cyl_waves w
        JOIN cyl_plants p ON p.wave_id = w.id AND p.accession_id IS NOT NULL
        JOIN cyl_scans s ON s.plant_id = p.id
        JOIN cyl_scan_traits cst ON cst.scan_id = s.id
        JOIN cyl_scan_latest_source l ON l.scan_id = cst.scan_id
            AND cst.source_id IS NOT DISTINCT FROM l.max_source_id
        WHERE w.experiment_id = %s AND cst.trait_id IS NOT NULL
        """,
        (experiment_id,),
    )
    return cur.fetchone()[0]


def test_refresh_populates_counts_matching_live_computation(pg_conn):
    with pg_conn.cursor() as cur:
        experiment_id, _scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(
            cur,
            imgs,
            "orig",
            traits=[_trait("length", 1.0), _trait("width", 2.0)],
        )
        _refresh(cur)
        assert _n_traits(cur, experiment_id) == 2 == _live_n_traits(cur, experiment_id)
    pg_conn.rollback()


def test_experiment_with_no_data_has_no_row(pg_conn):
    with pg_conn.cursor() as cur:
        experiment_id, _scan_id, _imgs = _seed_experiment_scan(cur)
        _refresh(cur)
        assert _n_traits(cur, experiment_id) is None
    pg_conn.rollback()


def test_experiment_that_loses_all_data_is_removed_on_next_refresh(pg_conn):
    with pg_conn.cursor() as cur:
        experiment_id, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        _refresh(cur)
        assert _n_traits(cur, experiment_id) == 1

        cur.execute("DELETE FROM cyl_scan_traits WHERE scan_id=%s", (scan_id,))
        _refresh(cur)
        assert _n_traits(cur, experiment_id) is None
    pg_conn.rollback()


def test_refresh_reflects_new_latest_source_not_old(pg_conn):
    with pg_conn.cursor() as cur:
        experiment_id, _scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        _refresh(cur)
        assert _n_traits(cur, experiment_id) == 1

        # A rerun that measures two NEW traits and doesn't repeat "length" for the latest source.
        _deliver(cur, imgs, "reproc", traits=[_trait("width", 2.0), _trait("height", 3.0)])
        # Before the next refresh, the cache still reflects the OLD state.
        assert _n_traits(cur, experiment_id) == 1
        _refresh(cur)
        assert _n_traits(cur, experiment_id) == 2 == _live_n_traits(cur, experiment_id)
    pg_conn.rollback()


def test_cross_experiment_isolation(pg_conn):
    with pg_conn.cursor() as cur:
        exp_a, _scan_a, imgs_a = _seed_experiment_scan(cur)
        exp_b, _scan_b, imgs_b = _seed_experiment_scan(cur)
        _deliver(cur, imgs_a, "a", traits=[_trait("length", 1.0)])
        _deliver(cur, imgs_b, "b", traits=[_trait("length", 1.0), _trait("width", 2.0)])
        _refresh(cur)
        assert _n_traits(cur, exp_a) == 1
        assert _n_traits(cur, exp_b) == 2
    pg_conn.rollback()


def test_null_accession_plant_excluded(pg_conn):
    with pg_conn.cursor() as cur:
        experiment_id, _scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])

        # A second plant/scan in the same experiment with NO accession.
        cur.execute(
            "SELECT wave_id FROM cyl_plants WHERE id = (SELECT plant_id FROM cyl_scans WHERE id=%s)",
            (_scan_id,),
        )
        (wave_id,) = cur.fetchone()
        cur.execute(
            "INSERT INTO cyl_plants (wave_id, accession_id, germ_day, qr_code) "
            "VALUES (%s, NULL, 5, 'qr-null-acc') RETURNING id"
        , (wave_id,))
        plant_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days) "
            "VALUES (%s, '2026-01-01', 10) RETURNING id",
            (plant_id,),
        )
        orphan_scan_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_images (scan_id) VALUES (%s) RETURNING id", (orphan_scan_id,)
        )
        orphan_img = cur.fetchone()[0]
        _deliver(cur, [orphan_img], "orphan", traits=[_trait("unique_trait_xyz", 9.0)])

        _refresh(cur)
        assert _n_traits(cur, experiment_id) == 1  # "unique_trait_xyz" excluded
    pg_conn.rollback()


def test_no_trigger_invokes_refresh_on_write(pg_conn):
    with pg_conn.cursor() as cur:
        experiment_id, _scan_id, imgs = _seed_experiment_scan(cur)
        _refresh(cur)  # establish an empty baseline (no row yet)
        assert _n_traits(cur, experiment_id) is None

        _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        # No refresh called -- the cache must still show no row for this experiment.
        assert _n_traits(cur, experiment_id) is None
    pg_conn.rollback()


def test_refresh_function_search_path_is_pinned(pg_conn):
    """No regression test previously guarded this -- caught in round-4 review: unlike
    maintain_cyl_scan_latest_source (test_cyl_scan_latest_source.py's
    test_trigger_function_metadata), this SECURITY DEFINER function had no test confirming its
    `SET search_path` survives a future edit."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT prosecdef, proconfig FROM pg_proc "
            "WHERE proname = 'refresh_cyl_experiment_trait_counts'"
        )
        prosecdef, proconfig = cur.fetchone()
        assert prosecdef is True
        assert any(c.startswith("search_path=") for c in (proconfig or []))
    pg_conn.rollback()


def test_concurrent_refreshes_do_not_raise_duplicate_key(pg_conninfo, pg_conn):
    """Caught in round-2 review, reproduced empirically against a local Postgres before the fix:
    refresh_cyl_experiment_trait_counts()'s DELETE-then-INSERT had no lock/ON CONFLICT, so two
    overlapping calls (e.g. an overlapping workflow_dispatch + scheduled run) raced -- the second
    call's DELETE found nothing left to delete (the first call's rows are new tuples outside its
    snapshot after the first commits), so its INSERT collided on the experiment_id PK with rows
    the first call had already committed ("duplicate key value violates unique constraint
    cyl_experiment_trait_counts_pkey"). pg_advisory_xact_lock(0, hashtext(...)) now serializes calls.

    Uses two real connections with explicit interleaving (not just "call it twice sequentially"),
    since the race only manifests when the second call's DELETE runs concurrently with the first's
    still-open transaction.

    Round-3 review flagged the original version of this test: it used a fixed `time.sleep(0.1)`
    before starting B and only ever asserted the ABSENCE of an error, which would also pass if the
    lock were silently a no-op and the two calls simply got lucky not to race on this particular
    run (timing-dependent, not a proof of blocking). Strengthened two ways: (1) B's call runs
    directly (not via a thread) up to the point where it must block, and `pg_locks` is queried
    from a third, independent connection to confirm a NON-granted waiter row exists for the SAME
    lock key (classid=0, objid=hashtext('refresh_cyl_experiment_trait_counts'), objsubid=2 -- the
    two-int form's signature, confirmed via direct psql introspection) held by A's own backend pid
    -- proving B is blocked on THIS lock, not just blocked on something; (2) a `b_done` event
    (rather than a fixed sleep) proves B was still blocked immediately before A commits and
    unblocked immediately after, matching the sibling concurrency tests' pattern in
    test_cyl_scan_latest_source.py."""
    with pg_conn.cursor() as cur:
        experiment_id, _scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
    pg_conn.commit()

    conn_a = psycopg.connect(pg_conninfo)
    conn_b = psycopg.connect(pg_conninfo)
    try:
        cur_a = conn_a.cursor()
        cur_a.execute("SELECT pg_backend_pid()")
        a_pid = cur_a.fetchone()[0]
        # A's call is uncontested (B hasn't started), so it completes and stays open, holding the
        # advisory lock until we commit below.
        cur_a.execute("SELECT public.refresh_cyl_experiment_trait_counts()")

        errors = {}
        b_done = threading.Event()

        def _run_b():
            try:
                conn_b.execute("SELECT public.refresh_cyl_experiment_trait_counts()")
                conn_b.commit()
            except Exception as e:  # noqa: BLE001 -- capturing for the assertion below
                errors["b"] = e
                conn_b.rollback()
            finally:
                b_done.set()

        b_thread = threading.Thread(target=_run_b)
        b_thread.start()
        assert not b_done.wait(timeout=0.5)  # B genuinely blocked, not just slow

        with pg_conn.cursor() as cur:
            # A holds the lock, granted; some OTHER backend (B) is waiting on the SAME lock key.
            cur.execute(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype='advisory' AND classid=0 "
                "  AND objid=hashtext('refresh_cyl_experiment_trait_counts') AND objsubid=2 "
                "  AND granted AND pid=%s",
                (a_pid,),
            )
            assert cur.fetchone()[0] == 1, "A should hold the two-int advisory lock, granted"
            cur.execute(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype='advisory' AND classid=0 "
                "  AND objid=hashtext('refresh_cyl_experiment_trait_counts') AND objsubid=2 "
                "  AND NOT granted AND pid != %s",
                (a_pid,),
            )
            assert cur.fetchone()[0] == 1, "B should be waiting on the SAME advisory lock key as A"

        conn_a.commit()
        assert b_done.wait(timeout=5.0)  # B proceeds once A releases the lock
        b_thread.join()

        assert errors == {}, f"concurrent refresh raised: {errors}"

        with pg_conn.cursor() as cur:
            assert _n_traits(cur, experiment_id) == 1
    finally:
        conn_a.close()
        conn_b.close()
        with pg_conn.cursor() as cur:
            _cleanup_seeded_experiment(cur, experiment_id)
            cur.execute("SELECT public.refresh_cyl_experiment_trait_counts()")
        pg_conn.commit()


def test_anon_sees_no_rows_despite_real_data_existing(pg_conn):
    """RLS enabled with no anon policy means SELECT succeeds but is silently filtered to zero
    rows -- confirmed rather than assumed from the migration's policy list."""
    with pg_conn.cursor() as cur:
        experiment_id, _scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("length", 1.0)])
        _refresh(cur)
        cur.execute("SET LOCAL ROLE anon")
        cur.execute(
            "SELECT n_traits FROM cyl_experiment_trait_counts WHERE experiment_id=%s",
            (experiment_id,),
        )
        assert cur.fetchall() == []
        cur.execute("RESET ROLE")
    pg_conn.rollback()


def test_anon_cannot_write_despite_the_raw_table_grant(pg_conn):
    """Same regression as cyl_scan_latest_source's equivalent test: Supabase's default privileges
    give anon a raw INSERT grant on this table regardless of any policy; RLS (not the grant) is
    what actually blocks an unauthenticated caller from writing fabricated counts directly."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege('anon', 'cyl_experiment_trait_counts', 'INSERT')"
        )
        assert cur.fetchone()[0] is True  # the raw grant genuinely exists

        cur.execute("SET LOCAL ROLE anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO cyl_experiment_trait_counts (experiment_id, n_traits) VALUES (-1, 999)"
            )
    pg_conn.rollback()


def test_anon_cannot_truncate(pg_conn):
    """RLS does NOT govern TRUNCATE -- same fix and reasoning as cyl_scan_latest_source's
    equivalent test (caught in round-3 review)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege('anon', 'cyl_experiment_trait_counts', 'TRUNCATE')"
        )
        assert cur.fetchone()[0] is False

        cur.execute("SET LOCAL ROLE anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("TRUNCATE public.cyl_experiment_trait_counts")
    pg_conn.rollback()


def test_bloom_admin_can_write_directly_to_cyl_experiment_trait_counts(pg_conn):
    """Same gap and fix as cyl_scan_latest_source's equivalent test (found in round-5 review):
    the `FOR ALL TO bloom_admin` RLS policy had no matching table-level grant backing it --
    bloom_admin had only SELECT here, confirmed via information_schema.role_table_grants, because
    this table is created by a different role than whichever one gave cyl_scan_traits its
    bloom_admin CRUD grant, and this repo's default-privileges rule for bloom_admin never fires for
    a table created by that other role. No existing test caught this since refresh_cyl_experiment_
    trait_counts() itself is SECURITY DEFINER and writes as its owner, not as bloom_admin."""
    with pg_conn.cursor() as cur:
        experiment_id, _scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])

        cur.execute("SET LOCAL ROLE bloom_admin")
        cur.execute(
            "INSERT INTO cyl_experiment_trait_counts (experiment_id, n_traits) VALUES (%s, %s)",
            (experiment_id, 1),
        )
        assert cur.rowcount == 1
        cur.execute(
            "UPDATE cyl_experiment_trait_counts SET n_traits = %s WHERE experiment_id = %s",
            (2, experiment_id),
        )
        assert cur.rowcount == 1
        cur.execute(
            "DELETE FROM cyl_experiment_trait_counts WHERE experiment_id = %s", (experiment_id,)
        )
        assert cur.rowcount == 1
        cur.execute("RESET ROLE")
    pg_conn.rollback()


def test_migration_adds_no_read_role_execute_grant(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.role_routine_grants "
            "WHERE routine_name = 'refresh_cyl_experiment_trait_counts' "
            "AND grantee IN ('bloom_agent', 'bloom_user', 'authenticated', 'PUBLIC')"
        )
        assert cur.fetchone()[0] == 0
        # 'anon' is a distinct grantee from 'PUBLIC' in role_routine_grants -- the IN-list above
        # does not imply anon is covered. Caught in round-2 review: this is exactly the property
        # the REVOKE ... FROM PUBLIC, anon, authenticated (20260817140000...sql) exists to
        # guarantee, and a future edit that dropped anon from that REVOKE would leave this test
        # still passing at 0 while anon silently regained EXECUTE.
        cur.execute(
            "SELECT has_function_privilege('anon', 'refresh_cyl_experiment_trait_counts()', 'EXECUTE')"
        )
        assert cur.fetchone()[0] is False
    pg_conn.rollback()


def test_migration_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='cyl_experiment_trait_counts'"
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_rollback_guard_blocks_out_of_order_rollback(pg_conn):
    """This migration's rollback must refuse to run while get_experiment_summary_counts
    (20260817150000) still references cyl_experiment_trait_counts -- proves the "ROLLBACK ORDER"
    guard in the rollback SQL is real, not just a comment. Before this guard existed, rolling this
    migration back in isolation would drop the table out from under that RPC's unpinned path
    silently at DROP time, only failing later, at the RPC's next call, with an unhelpful
    "relation does not exist" -- the guard turns that into an immediate, actionable error instead."""
    with pg_conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException, match="Roll back 20260817150000 first"):
            cur.execute(_sql_body(ROLLBACK))
    pg_conn.rollback()


def test_rollback_restores_prior_state(pg_conn):
    """Exercises the full, documented order (20260817150000's rollback first, then this one) rather
    than this migration in isolation -- rolling this one back alone is now guarded against (see the
    sibling test above)."""
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(REWRITE_ROLLBACK))
        cur.execute(_sql_body(ROLLBACK))
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='cyl_experiment_trait_counts'"
        )
        assert cur.fetchone()[0] == 0

        # Re-apply both, in forward order, so the rest of the suite (which assumes this change is
        # fully live) still works -- and confirm real function behavior is restored, not just the
        # object's existence.
        cur.execute(_sql_body(MIGRATION))
        cur.execute(_sql_body(REWRITE_MIGRATION))
        exp, _scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        _refresh(cur)
        assert _n_traits(cur, exp) == 1
    pg_conn.rollback()
