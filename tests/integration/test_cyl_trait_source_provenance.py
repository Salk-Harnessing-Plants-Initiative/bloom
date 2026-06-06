"""
Integration tests for change `add-cyl-trait-source-provenance`.

`cyl_trait_sources` gains a nullable `metadata jsonb` column and a nullable
`idempotency_key text` column with a UNIQUE constraint and a CHECK rejecting the
empty string. These tests assert the column types, the jsonb round-trip / opaque
storage, the UNIQUE + CHECK behavior, that legacy `name`-only rows stay valid, and
that the companion rollback script restores the prior shape.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT — it
cannot reach a remote/production database. Every test mutates inside the fixture's
uncommitted transaction and rolls back, so the local database is left untouched.

Runs in CI's `compose-health-check` job after migrations are applied
(`uv run --extra test pytest tests/integration/ -v`).
"""

import re
from pathlib import Path

import pytest

# Skip the whole module if psycopg isn't available (e.g. local dev without the
# `test` extra); the DB-configured path still exercises it in CI.
psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent


def _column_type(cur, column: str) -> str | None:
    cur.execute(
        """
        SELECT data_type
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'cyl_trait_sources'
           AND column_name  = %s
        """,
        (column,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def test_metadata_and_idempotency_key_columns_exist(pg_conn):
    with pg_conn.cursor() as cur:
        assert _column_type(cur, "metadata") == "jsonb"
        assert _column_type(cur, "idempotency_key") == "text"
    pg_conn.rollback()


def test_metadata_round_trips_a_json_object(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cyl_trait_sources (name, metadata)
            VALUES ('rt', '{"a": 1}'::jsonb)
            RETURNING jsonb_typeof(metadata), metadata
            """
        )
        typeof, value = cur.fetchone()
        assert typeof == "object"
        assert value == {"a": 1}
    pg_conn.rollback()


def test_metadata_accepts_non_object_jsonb(pg_conn):
    # Opaque jsonb: the column performs no DB-layer shape validation.
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO cyl_trait_sources (name, metadata) VALUES ('arr', '[1,2]'::jsonb)")
        cur.execute("INSERT INTO cyl_trait_sources (name, metadata) VALUES ('scalar', '42'::jsonb)")
    pg_conn.rollback()


def test_duplicate_non_null_idempotency_key_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO cyl_trait_sources (name, idempotency_key) VALUES ('a', 'dup-key')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute("INSERT INTO cyl_trait_sources (name, idempotency_key) VALUES ('b', 'dup-key')")
    pg_conn.rollback()  # clear the aborted transaction


def test_multiple_null_idempotency_keys_allowed(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO cyl_trait_sources (name, idempotency_key) VALUES ('n1', NULL)")
        cur.execute("INSERT INTO cyl_trait_sources (name, idempotency_key) VALUES ('n2', NULL)")
        cur.execute("SELECT count(*) FROM cyl_trait_sources WHERE idempotency_key IS NULL")
        assert cur.fetchone()[0] >= 2
    pg_conn.rollback()


def test_empty_string_idempotency_key_rejected(pg_conn):
    # The contract defaults idempotency_key to "" — the CHECK must reject it so
    # keyless runs cannot all collide onto a single source row.
    with pg_conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("INSERT INTO cyl_trait_sources (name, idempotency_key) VALUES ('e', '')")
    pg_conn.rollback()


def test_legacy_name_only_insert_stays_valid(pg_conn):
    # Existing inserts keep working; legacy rows have NULL metadata + key.
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cyl_trait_sources (name) VALUES ('legacy') "
            "RETURNING metadata, idempotency_key"
        )
        metadata, idempotency_key = cur.fetchone()
        assert metadata is None
        assert idempotency_key is None
    pg_conn.rollback()


def test_named_constraints_exist(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'public.cyl_trait_sources'::regclass"
        )
        names = {row[0] for row in cur.fetchall()}
    assert "cyl_trait_sources_idempotency_key_key" in names
    assert "cyl_trait_sources_idempotency_key_nonempty" in names
    pg_conn.rollback()


def _find_rollback_sql() -> Path | None:
    matches = sorted(
        (REPO_ROOT / "supabase" / "rollbacks").glob(
            "*_add_cyl_trait_source_provenance_rollback.sql"
        )
    )
    return matches[-1] if matches else None


def test_rollback_script_restores_prior_shape(pg_conn):
    """Self-contained: CI only rolls forward, so apply the rollback script's body
    inside an uncommitted transaction, assert the columns + constraints are gone,
    then ROLLBACK so the schema (and other tests) are unaffected."""
    rollback_path = _find_rollback_sql()
    if rollback_path is None:
        pytest.skip("rollback script not written yet")

    # Strip the BEGIN;/COMMIT; wrapper so we stay inside pg_conn's own transaction.
    body = "\n".join(
        line
        for line in rollback_path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )
    with pg_conn.cursor() as cur:
        cur.execute(body)
        assert _column_type(cur, "metadata") is None
        assert _column_type(cur, "idempotency_key") is None
        cur.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'public.cyl_trait_sources'::regclass"
        )
        names = {row[0] for row in cur.fetchall()}
        assert "cyl_trait_sources_idempotency_key_key" not in names
        assert "cyl_trait_sources_idempotency_key_nonempty" not in names
    pg_conn.rollback()  # restore the schema — leave the DB untouched
