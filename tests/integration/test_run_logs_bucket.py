"""
Integration tests for the `run-logs` Supabase Storage bucket, where each RNA-seq step uploads
its log (`rnaseq/<argo workflow name>/<step>.log`) for the run page to read.

The pipeline (`bloom_workflows`) uploads and overwrites; `bloom_user` and `bloom_agent` read;
only `bloom_admin` deletes. `bloom_writer` gets no policy of its own here, but keeps the
blanket storage.objects INSERT/UPDATE it has on every bucket (20260519130000), so it can
write but not delete.

LOCAL ONLY: `pg_conn` connects as `supabase_admin` (BYPASSRLS) and every test rolls back.
Tests seed the bucket row themselves so an object row's foreign key holds whether or not
the migration has been applied, which lets them fail red on the policies alone.
"""

import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
BUCKET = "run-logs"
LOG = "rnaseq/scrna-cellranger-staging-5-8b939a02/count.log"


def _seed_bucket(cur):
    cur.execute(
        "INSERT INTO storage.buckets (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
        (BUCKET, BUCKET),
    )


def _insert_object(cur, name: str):
    cur.execute(
        "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s) RETURNING id",
        (BUCKET, name),
    )
    return cur.fetchone()[0]


def test_the_bucket_is_private(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT public FROM storage.buckets WHERE id = %s", (BUCKET,))
        assert cur.fetchone() == (False,)
    pg_conn.rollback()


def test_the_pipeline_can_upload_read_back_and_overwrite(pg_conn):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        cur.execute("SET LOCAL ROLE bloom_workflows")
        oid = _insert_object(cur, LOG)
        cur.execute("SELECT name FROM storage.objects WHERE id = %s", (oid,))
        assert cur.fetchone() == (LOG,), "bloom_workflows can't read back its upload"
        # Each periodic upload overwrites the same object; its name doesn't change.
        cur.execute(
            "UPDATE storage.objects SET metadata = %s::jsonb WHERE id = %s",
            ('{"size": 2048}', oid),
        )
        assert cur.rowcount == 1, "bloom_workflows can't overwrite its upload"
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent"])
def test_users_and_the_agent_can_read(pg_conn, role):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        oid = _insert_object(cur, LOG)  # as supabase_admin
        cur.execute(f"SET LOCAL ROLE {role}")
        cur.execute("SELECT name FROM storage.objects WHERE id = %s", (oid,))
        assert cur.fetchone() == (LOG,), f"{role} can't read a run log"
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent"])
def test_users_and_the_agent_cannot_upload(pg_conn, role):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        cur.execute(f"SET LOCAL ROLE {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _insert_object(cur, f"rnaseq/{role}/should-not-upload.log")
    pg_conn.rollback()


@pytest.mark.parametrize(
    "role", ["bloom_user", "bloom_agent", "bloom_writer", "bloom_workflows"]
)
def test_no_one_but_an_admin_can_delete(pg_conn, role):
    """A role without a DELETE grant raises; bloom_writer has the table grant but no
    DELETE policy, so RLS filters its delete to zero rows. Either way the log survives."""
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        oid = _insert_object(cur, LOG)  # as supabase_admin
        cur.execute(f"SET LOCAL ROLE {role}")
        try:
            cur.execute("DELETE FROM storage.objects WHERE id = %s", (oid,))
        except psycopg.errors.InsufficientPrivilege:
            pg_conn.rollback()
            return
        assert cur.rowcount == 0, f"{role} deleted a run log"
    pg_conn.rollback()


def test_an_admin_can_prune_old_logs(pg_conn):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        cur.execute("SET LOCAL ROLE bloom_admin")
        # Storage's guard against direct deletes; the Storage API sets the same.
        cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
        oid = _insert_object(cur, LOG)
        cur.execute("DELETE FROM storage.objects WHERE id = %s", (oid,))
        assert cur.rowcount == 1
    pg_conn.rollback()


def test_the_bucket_has_exactly_its_policies(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT policyname, cmd, roles::text FROM pg_policies "
            "WHERE schemaname = 'storage' AND tablename = 'objects' "
            "AND (qual LIKE %s OR with_check LIKE %s)",
            (f"%'{BUCKET}'%", f"%'{BUCKET}'%"),
        )
        rows = cur.fetchall()
    pg_conn.rollback()
    pairs = {
        (role, cmd)
        for _name, cmd, roles in rows
        for role in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", roles)
    }
    assert pairs == {
        ("bloom_admin", "ALL"),
        ("bloom_agent", "SELECT"),
        ("bloom_user", "SELECT"),
        ("bloom_workflows", "SELECT"),
        ("bloom_workflows", "INSERT"),
        ("bloom_workflows", "UPDATE"),
    }


def _rollback_sql() -> str:
    [path] = sorted(
        (REPO_ROOT / "supabase" / "rollbacks").glob(
            "*_create_run_logs_bucket_rollback.sql"
        )
    )
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def test_the_rollback_drops_an_empty_bucket_and_its_policies(pg_conn):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        cur.execute(_rollback_sql())
        cur.execute("SELECT 1 FROM storage.buckets WHERE id = %s", (BUCKET,))
        assert cur.fetchone() is None
        cur.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname = 'storage' "
            "AND tablename = 'objects' AND policyname LIKE %s",
            ("%run_logs",),
        )
        assert cur.fetchone() == (0,)
    pg_conn.rollback()


def test_the_rollback_refuses_a_bucket_that_holds_logs(pg_conn):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        _insert_object(cur, LOG)
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(_rollback_sql())
    pg_conn.rollback()
