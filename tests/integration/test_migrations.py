"""
Integration tests for the migration runner (Supabase CLI `db push`).

Requires the CI compose stack to be up with migrations already applied
by the `compose-health-check` job's `Apply database migrations` step.
Uses the `pg_conn` fixture from conftest.py which connects to 127.0.0.1
on POSTGRES_HOST_PORT.
"""

import glob
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent.parent


def test_tracking_table_in_canonical_schema(pg_conn):
    """
    `supabase db push` MUST create its tracking table at
    `supabase_migrations.schema_migrations` — the Supabase canonical location.
    The legacy local-dev `public._migrations` table MUST NOT be used here.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_schema = 'supabase_migrations'
               AND table_name   = 'schema_migrations'
            """
        )
        assert cur.fetchone() is not None, (
            "supabase_migrations.schema_migrations does not exist — "
            "`supabase db push` was not run, or it wrote to a different location"
        )


def test_all_migrations_recorded(pg_conn):
    """
    Every `supabase/migrations/*.sql` file must have a matching row in
    `supabase_migrations.schema_migrations` after `db push` runs. If the row
    count is less, `db push` silently skipped something.
    """
    migration_files = sorted(glob.glob(str(REPO_ROOT / "supabase/migrations/*.sql")))
    assert migration_files, "No migration files found; fixture/layout may have changed"

    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM supabase_migrations.schema_migrations")
        recorded = cur.fetchone()[0]

    assert recorded == len(migration_files), (
        f"Tracking table has {recorded} rows but there are "
        f"{len(migration_files)} migration files. Missing migrations will "
        f"silently be re-applied on the next deploy."
    )


def test_public_migrations_legacy_absent(pg_conn):
    """
    `public._migrations` is the LOCAL-DEV Makefile's tracking table, not
    Supabase's. It must not be present in CI / deploy DBs — having it around
    creates a divergence risk.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_schema = 'public'
               AND table_name   = '_migrations'
            """
        )
        assert cur.fetchone() is None, (
            "public._migrations exists — this is the local-dev Makefile's "
            "tracking table, not Supabase's canonical one. Investigate "
            "whether the CI DB was bootstrapped through the wrong path."
        )


def test_migration_timestamps_are_unique(pg_conn):
    """
    Timestamps in `supabase_migrations.schema_migrations` are the unique key.
    Duplicates would indicate two migration files with the same YYYYMMDDHHMMSS
    prefix — which Supabase CLI orders ambiguously.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT version, count(*) AS n
              FROM supabase_migrations.schema_migrations
             GROUP BY version
            HAVING count(*) > 1
            """
        )
        duplicates = cur.fetchall()
        assert not duplicates, (
            f"Duplicate timestamps in tracking table: {duplicates}. "
            f"Two migration files share a 14-digit prefix — rename one."
        )
