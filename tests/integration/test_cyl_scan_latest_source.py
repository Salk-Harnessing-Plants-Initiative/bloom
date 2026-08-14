"""
Integration tests for `cyl_scan_latest_source` (bloom#637, supersedes PR #654's Phase 1).

One row per scan (`scan_id` PK, `max_source_id`), maintained by a trigger on `cyl_scan_traits`,
instead of a boolean stored on every trait row. `cyl_scan_traits_source.is_latest` is now computed
by joining to this table (see `test_cyl_read_path.py` for the view's own equivalence tests) rather
than a live window aggregate.

LOCAL ONLY: the `pg_conn`/`pg_conninfo` fixtures connect to 127.0.0.1 on POSTGRES_HOST_PORT as
`supabase_admin` (BYPASSRLS); every test using `pg_conn` alone rolls back. The concurrency tests
use two independent `pg_conninfo` connections with real threads and explicit interleaving, and
clean up via explicit `DELETE` (their effects are committed, not rolled back).
"""

import re
import threading
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.test_cyl_read_path import (  # noqa: E402
    _deliver,
    _seed_experiment_scan,
    _trait,
)

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260814010000_create_cyl_scan_latest_source"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS}_rollback.sql"


def _sql_body(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def _max_source(cur, scan_id):
    cur.execute(
        "SELECT max_source_id FROM cyl_scan_latest_source WHERE scan_id=%s", (scan_id,)
    )
    row = cur.fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------- #
# Trigger maintenance
# --------------------------------------------------------------------------- #


def test_fresh_insert_creates_row_with_source_max(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        src = _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        assert _max_source(cur, scan_id) == src
    pg_conn.rollback()


def test_rerun_updates_max_source_to_new_higher_source(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        s1 = _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        s2 = _deliver(cur, imgs, "reproc", traits=[_trait("length", 2.0)])
        assert s2 > s1
        assert _max_source(cur, scan_id) == s2
    pg_conn.rollback()


def test_deleting_latest_promotes_next_highest(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        s1 = _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
        s2 = _deliver(cur, imgs, "reproc", traits=[_trait("length", 2.0)])
        cur.execute(
            "DELETE FROM cyl_scan_traits WHERE scan_id=%s AND source_id=%s", (scan_id, s2)
        )
        assert _max_source(cur, scan_id) == s1
    pg_conn.rollback()


def test_direct_bloom_admin_write_is_maintained(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, _imgs = _seed_experiment_scan(cur)
        cur.execute("INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id", (f"src-{uuid.uuid4().hex[:8]}",))
        source_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_traits (name) VALUES (%s) RETURNING id", (f"t-{uuid.uuid4().hex[:8]}",)
        )
        trait_id = cur.fetchone()[0]
        cur.execute("SET LOCAL ROLE bloom_admin")
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
            "VALUES (%s, %s, %s, %s)",
            (scan_id, source_id, trait_id, 1.23),
        )
        cur.execute("RESET ROLE")
        assert _max_source(cur, scan_id) == source_id
    pg_conn.rollback()


def test_trigger_function_metadata(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT prosecdef, proconfig FROM pg_proc "
            "WHERE proname = 'maintain_cyl_scan_latest_source'"
        )
        prosecdef, proconfig = cur.fetchone()
        assert prosecdef is True
        assert any(c.startswith("search_path=") for c in (proconfig or []))
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Backfill correctness
# --------------------------------------------------------------------------- #


def test_backfill_matches_hand_computed_oracle(pg_conn):
    with pg_conn.cursor() as cur:
        # Seed pre-existing data directly, bypassing the trigger, to simulate un-backfilled rows.
        cur.execute(
            "ALTER TABLE cyl_scan_traits DISABLE TRIGGER maintain_cyl_scan_latest_source_after_write"
        )
        scan_ids = []
        for _ in range(3):
            _, scan_id, _imgs = _seed_experiment_scan(cur)
            scan_ids.append(scan_id)
            for i in range(2):
                cur.execute("INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id", (f"src-{uuid.uuid4().hex[:8]}",))
                source_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO cyl_traits (name) VALUES (%s) RETURNING id",
                    (f"t-{uuid.uuid4().hex[:8]}",),
                )
                trait_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
                    "VALUES (%s, %s, %s, %s)",
                    (scan_id, source_id, trait_id, 1.0),
                )
        cur.execute("ALTER TABLE cyl_scan_traits ENABLE TRIGGER maintain_cyl_scan_latest_source_after_write")
        cur.execute("DELETE FROM cyl_scan_latest_source WHERE scan_id = ANY(%s)", (scan_ids,))

        cur.execute(
            "INSERT INTO cyl_scan_latest_source (scan_id, max_source_id) "
            "SELECT scan_id, max(source_id) FROM cyl_scan_traits "
            "WHERE scan_id = ANY(%s) GROUP BY scan_id "
            "ON CONFLICT (scan_id) DO UPDATE SET max_source_id = EXCLUDED.max_source_id",
            (scan_ids,),
        )

        for scan_id in scan_ids:
            cur.execute(
                "SELECT max(source_id) FROM cyl_scan_traits WHERE scan_id=%s", (scan_id,)
            )
            (oracle,) = cur.fetchone()
            assert _max_source(cur, scan_id) == oracle
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Concurrency — the exact race reproduced empirically during design (design.md D2)
# --------------------------------------------------------------------------- #


def test_concurrent_first_insert_to_same_new_scan_converges_to_true_max(pg_conninfo, pg_conn):
    """Two connections both deliver the FIRST-EVER rows for the same brand-new scan_id, under
    different source_ids, interleaved so both are in-flight before either commits. The trigger's
    advisory lock must serialize these so the final max_source_id is the true higher of the two
    -- not whichever transaction committed last with a value computed before it could see the
    other's data (the exact race this design's pg_advisory_xact_lock(scan_id) closes)."""
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
    pg_conn.commit()  # scan/images must be visible to both connections below

    conn_a = psycopg.connect(pg_conninfo)
    conn_b = psycopg.connect(pg_conninfo)
    try:
        cur_a = conn_a.cursor()
        cur_b = conn_b.cursor()

        def _seed_trait_row(cur, label):
            cur.execute("INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id", (f"src-{uuid.uuid4().hex[:8]}",))
            source_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO cyl_traits (name) VALUES (%s) RETURNING id",
                (f"{label}-{uuid.uuid4().hex[:8]}",),
            )
            trait_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
                "VALUES (%s, %s, %s, %s)",
                (scan_id, source_id, trait_id, 1.0),
            )
            return source_id

        # A inserts first and holds its row's advisory-lock-serialized upsert uncommitted.
        source_a = _seed_trait_row(cur_a, "a")

        b_done = threading.Event()
        source_b_holder = {}

        def _run_b():
            source_b_holder["id"] = _seed_trait_row(cur_b, "b")
            conn_b.commit()
            b_done.set()

        b_thread = threading.Thread(target=_run_b)
        b_thread.start()
        assert not b_done.wait(timeout=0.5)  # B is genuinely blocked on A's advisory lock

        conn_a.commit()
        assert b_done.wait(timeout=5.0)  # B proceeds once A releases the lock
        b_thread.join()

        source_b = source_b_holder["id"]
        true_max = max(source_a, source_b)

        with pg_conn.cursor() as cur:
            assert _max_source(cur, scan_id) == true_max
    finally:
        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM cyl_scan_traits WHERE scan_id=%s", (scan_id,))
            cur.execute("DELETE FROM cyl_scan_latest_source WHERE scan_id=%s", (scan_id,))
        pg_conn.commit()
        conn_a.close()
        conn_b.close()


def test_concurrent_rerun_of_existing_scan_converges_to_true_max(pg_conninfo, pg_conn):
    """Same race, but for an EXISTING scan (already has a cyl_scan_latest_source row) instead of a
    brand-new one -- the shape PR #654's own testing originally found broken for its column-based
    design."""
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        original_source = _deliver(cur, imgs, "orig", traits=[_trait("length", 1.0)])
    pg_conn.commit()

    conn_a = psycopg.connect(pg_conninfo)
    conn_b = psycopg.connect(pg_conninfo)
    try:
        cur_a = conn_a.cursor()
        cur_b = conn_b.cursor()

        def _seed_rerun_row(cur, label):
            cur.execute("INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id", (f"src-{uuid.uuid4().hex[:8]}",))
            source_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO cyl_traits (name) VALUES (%s) RETURNING id",
                (f"{label}-{uuid.uuid4().hex[:8]}",),
            )
            trait_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
                "VALUES (%s, %s, %s, %s)",
                (scan_id, source_id, trait_id, 1.0),
            )
            return source_id

        source_a = _seed_rerun_row(cur_a, "a")

        b_done = threading.Event()
        source_b_holder = {}

        def _run_b():
            source_b_holder["id"] = _seed_rerun_row(cur_b, "b")
            conn_b.commit()
            b_done.set()

        b_thread = threading.Thread(target=_run_b)
        b_thread.start()
        assert not b_done.wait(timeout=0.5)

        conn_a.commit()
        assert b_done.wait(timeout=5.0)
        b_thread.join()

        source_b = source_b_holder["id"]
        true_max = max(original_source, source_a, source_b)

        with pg_conn.cursor() as cur:
            assert _max_source(cur, scan_id) == true_max
    finally:
        with pg_conn.cursor() as cur:
            cur.execute("DELETE FROM cyl_scan_traits WHERE scan_id=%s", (scan_id,))
            cur.execute("DELETE FROM cyl_scan_latest_source WHERE scan_id=%s", (scan_id,))
        pg_conn.commit()
        conn_a.close()
        conn_b.close()


# --------------------------------------------------------------------------- #
# Migration hygiene
# --------------------------------------------------------------------------- #


def test_migration_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='cyl_scan_latest_source'"
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname='maintain_cyl_scan_latest_source_after_write'"
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_rollback_restores_prior_state(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK))
        cur.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='cyl_scan_latest_source'"
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgname='maintain_cyl_scan_latest_source_after_write'"
        )
        assert cur.fetchone()[0] == 0
        # Re-apply so the rest of the suite (which assumes this migration is live) still works.
        cur.execute(_sql_body(MIGRATION))
    pg_conn.rollback()
