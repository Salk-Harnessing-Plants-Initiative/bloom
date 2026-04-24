"""
Integration tests for the migration runner (Supabase CLI `db push`).

Requires the CI compose stack to be up with migrations already applied
by the `compose-health-check` job's `Apply database migrations` step.
Uses the `pg_conn` fixture from conftest.py which connects to 127.0.0.1
on POSTGRES_HOST_PORT.
"""

import glob
import shutil
import subprocess
from pathlib import Path

import pytest


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
    `supabase_migrations.schema_migrations` after `db push` runs, and the
    tracking table must not contain orphan rows that don't correspond to
    a file on disk. A set comparison catches both directions — count
    equality passes trivially if the same total hides missing+extra rows.
    """
    migration_files = sorted(glob.glob(str(REPO_ROOT / "supabase/migrations/*.sql")))
    assert migration_files, "No migration files found; fixture/layout may have changed"

    # Filename → version (strip the first 14-digit timestamp prefix). The
    # Supabase CLI records this timestamp in the `version` column.
    file_versions = {Path(f).name.split("_", 1)[0] for f in migration_files}

    with pg_conn.cursor() as cur:
        cur.execute("SELECT version FROM supabase_migrations.schema_migrations")
        recorded_versions = {row[0] for row in cur.fetchall()}

    missing_from_db = file_versions - recorded_versions
    orphans_in_db = recorded_versions - file_versions

    assert not missing_from_db, (
        f"{len(missing_from_db)} migration file(s) NOT recorded in "
        f"supabase_migrations.schema_migrations — db push silently skipped: "
        f"{sorted(missing_from_db)}"
    )
    assert not orphans_in_db, (
        f"{len(orphans_in_db)} tracking-table row(s) have no matching file "
        f"on disk — structural drift: {sorted(orphans_in_db)}"
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


def test_db_push_is_idempotent(pg_conn, supabase_db_url):
    """
    `supabase db push` must be a no-op when every local migration is already
    recorded in the remote tracking table. If idempotency breaks, every deploy
    re-applies all migrations — the destructive ones (DROP TABLE, DROP COLUMN)
    would silently wipe real data on the second run.
    """
    if not shutil.which("supabase"):
        pytest.skip("supabase CLI not on PATH")

    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM supabase_migrations.schema_migrations")
        before = cur.fetchone()[0]

    # `--debug` works around supabase/cli#4839 — without it the CLI tries a
    # TLS handshake against a non-TLS Postgres and fails. The deploy
    # workflow passes --debug for the same reason.
    result = subprocess.run(
        ["supabase", "db", "push", "--db-url", supabase_db_url, "--debug", "--yes"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"`supabase db push` returned {result.returncode} on second run "
        f"(expected no-op). stderr tail: {result.stderr[-500:]}"
    )

    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM supabase_migrations.schema_migrations")
        after = cur.fetchone()[0]

    assert after == before, (
        f"tracking table changed (before={before}, after={after}) when no "
        f"migrations were pending — idempotency invariant broken"
    )
