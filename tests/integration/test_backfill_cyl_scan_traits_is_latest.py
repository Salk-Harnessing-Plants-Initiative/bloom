"""
Integration tests for `backfill_cyl_scan_traits_is_latest` (`fix-cyl-scan-traits-latest-rollup`,
bloom#637, Phase 1, M2) -- the batched, resumable procedure that populates `is_latest` for
`cyl_scan_traits` rows that predate the maintaining trigger.

The procedure issues internal `COMMIT`s per batch (design.md D4) so no single transaction holds a
lock for the full backfill -- Postgres only allows this when the calling session is NOT already
inside an explicit transaction block. `tests/integration/conftest.py`'s default `pg_conn` fixture
opens one implicitly on first use, so every test here uses a dedicated autocommit connection via
`pg_conninfo` instead, and cleans up with explicit `DELETE`s (not `pg_conn.rollback()`, which is a
no-op once the procedure has already committed).
"""

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260812020000_add_backfill_cyl_scan_traits_is_latest_procedure"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS}_rollback.sql"

PROCEDURE = "backfill_cyl_scan_traits_is_latest"


def _sql_body(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


class _Fixture:
    """Seeds a multi-scan, multi-source dataset directly (bypassing the write-back RPC and its
    trigger), leaving `is_latest` at its post-migration `false` default for every row -- exactly
    what real pre-existing data looks like the moment the schema migration lands, before any
    backfill has run. Cleans up everything it created via explicit DELETEs on an autocommit
    connection."""

    def __init__(self, conn):
        self.conn = conn
        self.scan_ids = []
        self.trait_ids = []
        self.token = uuid.uuid4().hex[:8]

    def _trait_id(self, cur, name):
        cur.execute(
            "INSERT INTO cyl_traits (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (name,),
        )
        cur.execute("SELECT id FROM cyl_traits WHERE name=%s", (name,))
        tid = cur.fetchone()[0]
        self.trait_ids.append(tid)
        return tid

    def seed_scan(self, cur, *, n_sources=2, traits=("length",)):
        cur.execute("INSERT INTO species DEFAULT VALUES RETURNING id")
        species_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_experiments (name, species_id) VALUES (%s, %s) RETURNING id",
            (f"exp-{self.token}-{len(self.scan_ids)}", species_id),
        )
        experiment_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, 1) RETURNING id",
            (experiment_id,),
        )
        wave_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO accessions (name) VALUES (%s) RETURNING id",
            (f"acc-{self.token}-{len(self.scan_ids)}",),
        )
        accession_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_plants (wave_id, accession_id, germ_day, qr_code) "
            "VALUES (%s, %s, 5, %s) RETURNING id",
            (wave_id, accession_id, f"qr-{self.token}-{len(self.scan_ids)}"),
        )
        plant_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days) "
            "VALUES (%s, '2026-01-01', 10) RETURNING id",
            (plant_id,),
        )
        scan_id = cur.fetchone()[0]
        self.scan_ids.append(scan_id)

        trait_ids = [self._trait_id(cur, name) for name in traits]
        source_ids = []
        # Disable the maintaining trigger for these inserts -- it would otherwise immediately
        # correct is_latest on every row, defeating the point of simulating genuinely
        # pre-existing, un-backfilled legacy data (which by construction predates the trigger).
        # This is a catalog-level change, not session-scoped, so it MUST be re-enabled in a
        # finally -- an assertion failure between disable and enable would otherwise leave the
        # trigger off for every other session on this database, not just this test.
        cur.execute(
            "ALTER TABLE cyl_scan_traits DISABLE TRIGGER maintain_is_latest_after_write"
        )
        try:
            for i in range(n_sources):
                cur.execute(
                    "INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id",
                    (f"src-{self.token}-{scan_id}-{i}",),
                )
                source_id = cur.fetchone()[0]
                source_ids.append(source_id)
                for trait_id in trait_ids:
                    cur.execute(
                        "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value, is_latest) "
                        "VALUES (%s, %s, %s, %s, false)",
                        (scan_id, source_id, trait_id, float(i)),
                    )
        finally:
            cur.execute(
                "ALTER TABLE cyl_scan_traits ENABLE TRIGGER maintain_is_latest_after_write"
            )
        return scan_id, source_ids

    def oracle(self, cur):
        """Hand-computed max(source_id) OVER (PARTITION BY scan_id), IS NOT DISTINCT FROM."""
        cur.execute(
            "SELECT scan_id, source_id, "
            "source_id IS NOT DISTINCT FROM max(source_id) OVER (PARTITION BY scan_id) "
            "FROM cyl_scan_traits WHERE scan_id = ANY(%s) ORDER BY scan_id, source_id",
            (self.scan_ids,),
        )
        return cur.fetchall()

    def actual(self, cur):
        cur.execute(
            "SELECT scan_id, source_id, is_latest FROM cyl_scan_traits "
            "WHERE scan_id = ANY(%s) ORDER BY scan_id, source_id",
            (self.scan_ids,),
        )
        return cur.fetchall()

    def cleanup(self, cur):
        cur.execute(
            "DELETE FROM cyl_scan_traits WHERE scan_id = ANY(%s)", (self.scan_ids,)
        )
        cur.execute(
            "DELETE FROM cyl_trait_sources WHERE name LIKE %s", (f"src-{self.token}-%",)
        )
        for scan_id in self.scan_ids:
            cur.execute("SELECT plant_id FROM cyl_scans WHERE id=%s", (scan_id,))
            row = cur.fetchone()
            cur.execute("DELETE FROM cyl_scans WHERE id=%s", (scan_id,))
            if row:
                cur.execute("DELETE FROM cyl_plants WHERE id=%s", (row[0],))
        cur.execute(
            "DELETE FROM accessions WHERE name LIKE %s", (f"acc-{self.token}-%",)
        )
        cur.execute(
            "DELETE FROM cyl_experiments WHERE name LIKE %s", (f"exp-{self.token}-%",)
        )


@pytest.fixture
def autocommit_conn(pg_conninfo):
    conn = psycopg.connect(pg_conninfo, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Requirement: One-time backfill populates is_latest without a long-held lock
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("batch_size", [10000, 1])
def test_backfill_matches_oracle_across_batch_sizes(autocommit_conn, batch_size):
    """Parametrized over a large and a deliberately tiny range width to force the loop across
    multiple iterations -- tests that every scan_id is covered exactly once with no off-by-one
    skip/double-count at a range boundary, NOT that a scan's rows get split across batches (which
    is structurally impossible: the procedure batches and groups by the same key, scan_id).
    """
    fx = _Fixture(autocommit_conn)
    with autocommit_conn.cursor() as cur:
        try:
            # A few scans with gaps between their scan_ids (real cyl_scans ids are not
            # necessarily contiguous once other tests/data have run/been deleted).
            for _ in range(4):
                fx.seed_scan(cur, n_sources=2, traits=("length", "width"))
            cur.execute(f"CALL {PROCEDURE}(%s)", (batch_size,))
            assert fx.actual(cur) == fx.oracle(cur)
        finally:
            fx.cleanup(cur)


def test_interrupted_backfill_resumes_correctly(autocommit_conn):
    fx = _Fixture(autocommit_conn)
    with autocommit_conn.cursor() as cur:
        try:
            fx.seed_scan(cur, n_sources=3, traits=("length",))
            cur.execute(f"CALL {PROCEDURE}(%s)", (10000,))
            correct = fx.actual(cur)
            # Simulate an interrupted run: reset every row back to the un-backfilled default.
            cur.execute(
                "UPDATE cyl_scan_traits SET is_latest = false WHERE scan_id = ANY(%s)",
                (fx.scan_ids,),
            )
            cur.execute(f"CALL {PROCEDURE}(%s)", (10000,))
            assert fx.actual(cur) == correct == fx.oracle(cur)
        finally:
            fx.cleanup(cur)


def test_migration_adds_no_write_capability():
    sql = MIGRATION.read_text().lower()
    assert "create policy" not in sql
    assert not re.search(r"grant\s+[^;]*\b(insert|update|delete|all)\b", sql)


def test_migration_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute("SELECT count(*) FROM pg_proc WHERE proname=%s", (PROCEDURE,))
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_rollback_restores_prior_state(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK))
        cur.execute("SELECT count(*) FROM pg_proc WHERE proname=%s", (PROCEDURE,))
        assert cur.fetchone()[0] == 0
        cur.execute(_sql_body(MIGRATION))
        cur.execute("SELECT count(*) FROM pg_proc WHERE proname=%s", (PROCEDURE,))
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()
