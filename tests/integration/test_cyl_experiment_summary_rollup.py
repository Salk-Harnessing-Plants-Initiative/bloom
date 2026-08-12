"""
Integration tests for the `cyl_experiment_summary_counts` rollup table
(`fix-cyl-scan-traits-latest-rollup`, bloom#637, Phase 1, M3).

Even with `is_latest` indexed, an unpinned `list_experiments()` call still has to join
cyl_scan_traits_source up through scans -> plants -> waves -> experiments and GROUP BY across
~26M "latest" rows -- the other half of the timeout. This adds a small per-experiment rollup table
(experiment_id, n_plants, n_traits), refreshed event-driven, scoped to the one experiment whose
underlying data just changed (piggybacking on the same trigger that maintains `is_latest`).

INERT IN PHASE 1: `get_experiment_summary_counts` is NOT rewritten to read this table yet (Phase 2,
gated on a staging backfill) -- these tests exercise the table and its maintenance directly.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT as `supabase_admin`
(BYPASSRLS) and every test rolls back, except the backfill-procedure tests (`CALL` needs autocommit
-- same reasoning as `test_backfill_cyl_scan_traits_is_latest.py`).
"""

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.test_cyl_experiment_summary_counts import (
    _seed_scan_no_accession,
)
from tests.integration.test_cyl_read_path import (
    _deliver,
    _seed_experiment,
    _seed_experiment_scan,
    _seed_scan_in,
    _trait,
)

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260812030000_create_cyl_experiment_summary_counts"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS}_rollback.sql"

HELPER = "compute_cyl_experiment_summary_counts_live"
BACKFILL_PROC = "backfill_cyl_experiment_summary_counts"


def _sql_body(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def _rollup_row(cur, experiment_id):
    cur.execute(
        "SELECT n_plants, n_traits FROM cyl_experiment_summary_counts WHERE experiment_id=%s",
        (experiment_id,),
    )
    return cur.fetchone()


def _live_counts(cur, experiment_id):
    cur.execute(
        f"SELECT n_plants, n_traits FROM {HELPER}(%s, NULL, NULL)", (experiment_id,)
    )
    return cur.fetchone()


# --------------------------------------------------------------------------- #
# Requirement: per-experiment summary rollup table
# --------------------------------------------------------------------------- #


def test_fresh_ingest_creates_matching_rollup_row(pg_conn):
    with pg_conn.cursor() as cur:
        exp, wave = _seed_experiment(cur)
        _, imgs = _seed_scan_in(cur, wave)
        _deliver(cur, imgs, "k", traits=[_trait("A", 1.0), _trait("B", 2.0)])
        assert _rollup_row(cur, exp) == (1, 2) == _live_counts(cur, exp)
    pg_conn.rollback()


def test_rerun_that_changes_latest_source_updates_rollup(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "old", traits=[_trait("A", 1.0)])
        assert _rollup_row(cur, exp) == (1, 1)
        _deliver(cur, imgs, "new", traits=[_trait("A", 1.0), _trait("B", 2.0)])
        assert _rollup_row(cur, exp) == (1, 2) == _live_counts(cur, exp)
    pg_conn.rollback()


def test_experiment_losing_all_data_has_rollup_row_removed(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("A", 1.0)])
        assert _rollup_row(cur, exp) is not None
        cur.execute(
            "DELETE FROM cyl_scan_traits WHERE scan_id IN "
            "(SELECT id FROM cyl_scans WHERE plant_id IN "
            "(SELECT id FROM cyl_plants WHERE wave_id IN "
            "(SELECT id FROM cyl_waves WHERE experiment_id=%s)))",
            (exp,),
        )
        assert _rollup_row(cur, exp) is None
    pg_conn.rollback()


def test_freshly_created_empty_experiment_never_gets_a_rollup_row(pg_conn):
    """Distinct from the deletion case above: an experiment that never had any data should not
    get a zero-valued row from the moment it's created -- a refresh implementation could pass the
    deletion case while still incorrectly emitting a row here."""
    with pg_conn.cursor() as cur:
        exp, _ = _seed_experiment(cur)  # no scans at all
        assert _rollup_row(cur, exp) is None
    pg_conn.rollback()


def test_two_experiments_writes_only_touch_their_own_rollup_row(pg_conn):
    with pg_conn.cursor() as cur:
        exp1, _, imgs1 = _seed_experiment_scan(cur)
        exp2, _, imgs2 = _seed_experiment_scan(cur)
        _deliver(cur, imgs1, "e1", traits=[_trait("A", 1.0)])
        assert _rollup_row(cur, exp1) == (1, 1)
        assert _rollup_row(cur, exp2) is None
        _deliver(cur, imgs2, "e2", traits=[_trait("A", 1.0), _trait("B", 2.0)])
        assert _rollup_row(cur, exp1) == (1, 1)
        assert _rollup_row(cur, exp2) == (1, 2)
    pg_conn.rollback()


def test_accession_null_plant_excluded_from_rollup(pg_conn):
    with pg_conn.cursor() as cur:
        exp, wave = _seed_experiment(cur)
        _, imgs_ok = _seed_scan_in(cur, wave)
        _, imgs_no_acc = _seed_scan_no_accession(cur, wave)
        _deliver(cur, imgs_ok, "ok", traits=[_trait("A", 1.0)])
        _deliver(cur, imgs_no_acc, "no-acc", traits=[_trait("B", 2.0)])
        assert _rollup_row(cur, exp) == (1, 1) == _live_counts(cur, exp)
    pg_conn.rollback()


def test_refresh_does_not_read_back_its_own_rollup_row(pg_conn):
    """design.md D7's self-reference-avoidance note: the refresh's own aggregation must compute
    from a live join (via the shared helper), not by calling the rollup-backed
    get_experiment_summary_counts RPC -- which would read cyl_experiment_summary_counts before
    this very refresh has written it. Verified here by corrupting the rollup row and confirming a
    subsequent write still refreshes it to the CORRECT value, not the corrupted one echoed back.
    """
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", traits=[_trait("A", 1.0)])
        cur.execute(
            "UPDATE cyl_experiment_summary_counts SET n_plants=999, n_traits=999 "
            "WHERE experiment_id=%s",
            (exp,),
        )
        assert _rollup_row(cur, exp) == (999, 999)
        # Any write that touches this experiment's scans re-triggers the refresh.
        _deliver(cur, imgs, "k2", traits=[_trait("A", 1.0), _trait("B", 2.0)])
        assert _rollup_row(cur, exp) == (1, 2)
    pg_conn.rollback()


def test_rollup_reflects_post_maintenance_is_latest_state(pg_conn):
    """Trigger-firing-order test, independent of whether the refresh is wired as a second
    statement in the same trigger or a second trigger: after a rerun changes which source is
    latest, the rollup must reflect the NEW latest state, not a snapshot from before is_latest
    finished updating for that write."""
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "old", traits=[_trait("A", 1.0), _trait("B", 2.0)])
        assert _rollup_row(cur, exp) == (1, 2)
        _deliver(cur, imgs, "new", traits=[_trait("A", 1.0)])  # drops B
        assert _rollup_row(cur, exp) == (1, 1) == _live_counts(cur, exp)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Requirement: one-time rollup backfill
# --------------------------------------------------------------------------- #


@pytest.fixture
def autocommit_conn(pg_conninfo):
    conn = psycopg.connect(pg_conninfo, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def test_rollup_backfill_matches_live_computation(autocommit_conn):
    token = uuid.uuid4().hex[:8]
    with autocommit_conn.cursor() as cur:
        exp_ids = []
        # Disable both cyl_scan_traits triggers for the raw inserts below -- otherwise the
        # refresh trigger would create each rollup row itself the moment the row is inserted,
        # and this test would pass even if the BACKFILL procedure itself were broken (it would
        # just find an already-correct row and no-op). Catalog-level, not session-scoped -- MUST
        # re-enable in the finally.
        cur.execute("ALTER TABLE cyl_scan_traits DISABLE TRIGGER USER")
        try:
            for i in range(3):
                cur.execute("INSERT INTO species DEFAULT VALUES RETURNING id")
                species_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO cyl_experiments (name, species_id) VALUES (%s, %s) RETURNING id",
                    (f"exp-rollupbf-{token}-{i}", species_id),
                )
                exp_id = cur.fetchone()[0]
                exp_ids.append(exp_id)
                cur.execute(
                    "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, 1) RETURNING id",
                    (exp_id,),
                )
                wave_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO accessions (name) VALUES (%s) RETURNING id",
                    (f"acc-rollupbf-{token}-{i}",),
                )
                accession_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO cyl_plants (wave_id, accession_id, germ_day, qr_code) "
                    "VALUES (%s, %s, 5, %s) RETURNING id",
                    (wave_id, accession_id, f"qr-rollupbf-{token}-{i}"),
                )
                plant_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days) "
                    "VALUES (%s, '2026-01-01', 10) RETURNING id",
                    (plant_id,),
                )
                scan_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id",
                    (f"src-rollupbf-{token}-{i}",),
                )
                source_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO cyl_traits (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                    (f"trait-rollupbf-{token}",),
                )
                cur.execute(
                    "SELECT id FROM cyl_traits WHERE name=%s",
                    (f"trait-rollupbf-{token}",),
                )
                trait_id = cur.fetchone()[0]
                # Direct insert bypasses the trigger's is_latest maintenance -- simulate rows that
                # predate the trigger and haven't been backfilled either (is_latest left false),
                # exactly what a pre-existing rollup backfill would need to handle correctly once
                # `is_latest`'s OWN backfill (section 3.1-3.4) has already run and is correct.
                cur.execute(
                    "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value, is_latest) "
                    "VALUES (%s, %s, %s, 1.0, true)",
                    (scan_id, source_id, trait_id),
                )
            cur.execute("ALTER TABLE cyl_scan_traits ENABLE TRIGGER USER")
            cur.execute(f"CALL {BACKFILL_PROC}(%s)", (10000,))
            for exp_id in exp_ids:
                cur.execute(
                    "SELECT n_plants, n_traits FROM cyl_experiment_summary_counts "
                    "WHERE experiment_id=%s",
                    (exp_id,),
                )
                rollup = cur.fetchone()
                cur.execute(
                    f"SELECT n_plants, n_traits FROM {HELPER}(%s, NULL, NULL)",
                    (exp_id,),
                )
                live = cur.fetchone()
                assert rollup == live == (1, 1)
        finally:
            cur.execute(
                "ALTER TABLE cyl_scan_traits ENABLE TRIGGER USER"
            )  # no-op if already on
            cur.execute(
                "DELETE FROM cyl_scan_traits WHERE trait_id IN "
                "(SELECT id FROM cyl_traits WHERE name=%s)",
                (f"trait-rollupbf-{token}",),
            )
            cur.execute(
                "DELETE FROM cyl_trait_sources WHERE name LIKE %s",
                (f"src-rollupbf-{token}-%",),
            )
            cur.execute(
                "DELETE FROM cyl_traits WHERE name=%s", (f"trait-rollupbf-{token}",)
            )
            cur.execute(
                "DELETE FROM cyl_experiment_summary_counts WHERE experiment_id = ANY(%s)",
                (exp_ids,),
            )
            for exp_id in exp_ids:
                cur.execute(
                    "DELETE FROM cyl_scans WHERE plant_id IN "
                    "(SELECT id FROM cyl_plants WHERE wave_id IN "
                    "(SELECT id FROM cyl_waves WHERE experiment_id=%s))",
                    (exp_id,),
                )
                cur.execute(
                    "DELETE FROM cyl_plants WHERE wave_id IN "
                    "(SELECT id FROM cyl_waves WHERE experiment_id=%s)",
                    (exp_id,),
                )
            cur.execute(
                "DELETE FROM accessions WHERE name LIKE %s",
                (f"acc-rollupbf-{token}-%",),
            )
            cur.execute(
                "DELETE FROM cyl_waves WHERE experiment_id = ANY(%s)", (exp_ids,)
            )
            cur.execute("DELETE FROM cyl_experiments WHERE id = ANY(%s)", (exp_ids,))


@pytest.mark.skip(
    reason=(
        "Premise doesn't hold yet in Phase 1, discovered while implementing this test: "
        "cyl_scan_traits_source.is_latest is still LIVE-computed (the WindowAgg) until Phase 2's "
        "M4 view cutover lands, so compute_cyl_experiment_summary_counts_live -- which joins "
        "through that view, not the stored column -- is always correct regardless of the stored "
        "column's backfill state. The real ordering constraint this spec scenario is protecting "
        "against only starts to matter once M4 is live: at that point the ROLLUP backfill must "
        "also run AFTER M4 (so its own query is cheap, matching Benfica's 'step 1 makes the "
        "refresh cheap enough to run' framing) but BEFORE M5's RPC rewrite goes live (so "
        "list_experiments() never reads an empty/stale rollup) -- a genuine third ordering "
        "constraint (M4 -> rollup backfill -> M5) not captured by the original two-phase design. "
        "See design.md's Open Questions for the flagged design gap this discovery produced; "
        "rewrite this test once that's resolved and the rollup backfill's real invocation point "
        "is settled."
    )
)
def test_rollup_backfill_ordering_gate_consequence():
    pass


# --------------------------------------------------------------------------- #
# Requirement: additive migration + rollback
# --------------------------------------------------------------------------- #


def test_migration_adds_no_extra_write_capability():
    sql = MIGRATION.read_text().lower()
    assert "create policy" not in sql


def test_migration_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='cyl_experiment_summary_counts'"
        )
        assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM pg_proc WHERE proname=%s", (HELPER,))
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_rollback_restores_prior_state(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK))
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='cyl_experiment_summary_counts'"
        )
        assert cur.fetchone()[0] == 0
        cur.execute(_sql_body(MIGRATION))
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='cyl_experiment_summary_counts'"
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()
