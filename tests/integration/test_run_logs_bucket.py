"""
Integration tests for the `run-logs` Supabase Storage bucket, where each RNA-seq step uploads
its log (`scrna/<argo workflow name>/<step>.log`) for the run page to read.

The pipeline (`bloom_workflows`) uploads and overwrites; `bloom_user` and `bloom_agent` read;
only `bloom_admin` deletes. `bloom_writer` gets no policy of its own here, but keeps the
blanket storage.objects INSERT/UPDATE it has on every bucket (20260519130000), so it can
write but not delete; that is accepted, and pinned below.

LOCAL ONLY: `pg_conn` connects as `supabase_admin` (BYPASSRLS) and every test rolls back.
Tests that write objects seed the bucket row themselves, so an object row's foreign key
holds whether or not the migration has been applied and they fail red on the policies alone.
The bucket-settings test reads the migration's own row.

Storage's `protect_objects_delete` trigger refuses every direct DELETE unless
`storage.allow_delete_query` is set (storage-api sets it on each request), so the delete
tests set it to reach the grants and policies.
"""

import json
import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
BUCKET = "run-logs"
LOG = "scrna/scrna-cellranger-staging-5-8b939a02/count.log"

# The statement storage-api v1.48.14 runs as the caller's role for an upload with upsert
# (the same one test_gravi_plate_video_write.py pins); each 30 s log upload is one of these.
_STORAGE_UPSERT = """
    INSERT INTO storage.objects
      (name, owner, owner_id, bucket_id, metadata, user_metadata, version)
    VALUES (%s, NULL, NULL, %s, %s, %s, %s)
    ON CONFLICT (name, bucket_id) DO UPDATE
      SET metadata = EXCLUDED.metadata,
          user_metadata = EXCLUDED.user_metadata,
          version = EXCLUDED.version,
          owner = EXCLUDED.owner,
          owner_id = EXCLUDED.owner_id
    RETURNING *
"""


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


def _upsert(cur, name: str, version: str):
    cur.execute(
        _STORAGE_UPSERT,
        (name, BUCKET, json.dumps({"size": len(version)}), json.dumps({}), version),
    )


def test_the_roles_under_test_do_not_bypass_rls(pg_conn):
    # Otherwise every "cannot" test below would pass for the wrong reason.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname IN "
            "('bloom_user', 'bloom_agent', 'bloom_writer', 'bloom_workflows', 'anon', 'authenticated')"
        )
        assert not any(bypass for _role, bypass in cur.fetchall())
    pg_conn.rollback()


def test_the_bucket_is_private_text_only_and_capped(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT public, file_size_limit, allowed_mime_types FROM storage.buckets WHERE id = %s",
            (BUCKET,),
        )
        assert cur.fetchone() == (False, 52428800, ["text/plain"])
    pg_conn.rollback()


def test_the_pipeline_can_upload_and_overwrite_as_storage_does(pg_conn):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        cur.execute("SET LOCAL ROLE bloom_workflows")
        _upsert(cur, LOG, "first")
        _upsert(cur, LOG, "second")
        cur.execute(
            "SELECT version FROM storage.objects WHERE bucket_id = %s AND name = %s",
            (BUCKET, LOG),
        )
        assert cur.fetchall() == [("second",)], (
            "the second upload didn't replace the first"
        )
    pg_conn.rollback()


def test_a_writer_can_upload_through_its_policy_on_every_bucket(pg_conn):
    # Accepted: writers' blanket INSERT/UPDATE reaches this bucket too.
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        cur.execute("SET LOCAL ROLE bloom_writer")
        _upsert(cur, LOG, "from-a-writer")
        assert cur.rowcount == 1
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent"])
def test_users_and_the_agent_can_read(pg_conn, role):
    # The agent also reads through its blanket policy, so only the policy-set test pins
    # agent_read_run_logs itself.
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        oid = _insert_object(cur, LOG)  # as supabase_admin
        cur.execute(f"SET LOCAL ROLE {role}")
        cur.execute("SELECT name FROM storage.objects WHERE id = %s", (oid,))
        assert cur.fetchone() == (LOG,), f"{role} can't read a run log"
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_no_one_signed_out_or_without_a_bloom_role_can_read(pg_conn, role):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        oid = _insert_object(cur, LOG)  # as supabase_admin
        cur.execute(f"SET LOCAL ROLE {role}")
        try:
            cur.execute("SELECT name FROM storage.objects WHERE id = %s", (oid,))
        except psycopg.errors.InsufficientPrivilege:
            pg_conn.rollback()
            return
        assert cur.fetchone() is None, f"{role} can read a run log"
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent"])
def test_users_and_the_agent_cannot_upload(pg_conn, role):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        cur.execute(f"SET LOCAL ROLE {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _insert_object(cur, f"scrna/{role}/should-not-upload.log")
    pg_conn.rollback()


@pytest.mark.parametrize(
    "role", ["bloom_user", "bloom_agent", "bloom_writer", "bloom_workflows"]
)
def test_no_one_but_an_admin_can_delete(pg_conn, role):
    """A role without a DELETE grant raises; a role with the grant but no DELETE policy is
    filtered to zero rows. Either way the log survives."""
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        oid = _insert_object(cur, LOG)  # as supabase_admin
        cur.execute("SET LOCAL storage.allow_delete_query = 'true'")
        cur.execute(f"SET LOCAL ROLE {role}")
        try:
            cur.execute("DELETE FROM storage.objects WHERE id = %s", (oid,))
        except psycopg.errors.InsufficientPrivilege:
            pg_conn.rollback()
            return
        assert cur.rowcount == 0, f"{role} deleted a run log"
    pg_conn.rollback()


def test_an_admin_can_prune_old_logs(pg_conn):
    # The admin's blanket policy also allows this, so only the policy-set test pins
    # admin_all_run_logs itself.
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        cur.execute("SET LOCAL ROLE bloom_admin")
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
