"""
Integration tests for adding public.species to the supabase_realtime publication, so the
Species page updates live: the migration adds it, re-applying it is a no-op, and the
rollback takes it out (also a no-op when it is already out). Bloom's signed-in role can
read species, which Realtime needs before it sends a row.

Each test applies the files inside its own transaction and rolls it back, so the database
is left unchanged.
"""

import pytest

from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

MIGRATION = _find_one("migrations", "*_enable_realtime_for_species.sql")
ROLLBACK = _find_one("rollbacks", "*_enable_realtime_for_species_rollback.sql")


def _published(cur) -> bool:
    cur.execute(
        "SELECT 1 FROM pg_publication_tables "
        "WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'species'"
    )
    return cur.fetchone() is not None


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


def test_species_is_in_the_realtime_publication(cur):
    assert _published(cur)


def test_reapplying_the_migration_is_a_no_op(cur):
    cur.execute(_sql_body(MIGRATION))
    assert _published(cur)


def test_the_rollback_takes_species_out_and_can_run_twice(cur):
    cur.execute(_sql_body(ROLLBACK))
    assert not _published(cur)
    cur.execute(_sql_body(ROLLBACK))
    assert not _published(cur)


def test_signed_in_bloom_users_can_read_species(cur):
    cur.execute("SELECT has_table_privilege('bloom_user', 'public.species', 'SELECT')")
    assert cur.fetchone()[0] is True
