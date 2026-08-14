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
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.test_cyl_read_path import (  # noqa: E402
    _deliver,
    _seed_experiment_scan,
    _trait,
)

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260814020000_create_cyl_experiment_trait_counts"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS}_rollback.sql"


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


def test_migration_adds_no_read_role_execute_grant(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.role_routine_grants "
            "WHERE routine_name = 'refresh_cyl_experiment_trait_counts' "
            "AND grantee IN ('bloom_agent', 'bloom_user', 'authenticated', 'PUBLIC')"
        )
        assert cur.fetchone()[0] == 0
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


def test_rollback_restores_prior_state(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK))
        cur.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='cyl_experiment_trait_counts'"
        )
        assert cur.fetchone()[0] == 0
        cur.execute(_sql_body(MIGRATION))
    pg_conn.rollback()
