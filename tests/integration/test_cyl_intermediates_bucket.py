"""
Integration tests for change `add-cyl-blob-upload` (bloom #407) — the
`cyl-intermediates` Supabase Storage bucket that holds the `.slp` bytes
`cyl_scan_intermediates.s3_location` points at.

Unlike `cyl_scan_intermediates` (the TABLE, locked to RPC-only writes — see
`test_cyl_scan_intermediates.py`), there is no `SECURITY DEFINER` RPC wrapping
Supabase Storage byte writes, so `bloom_writer` and `bloom_workflows` need
direct `storage.objects` INSERT/UPDATE for this bucket (mirroring the existing
`bloom_workflows`/`videos`-bucket precedent, `20260716000000_create_workflows_role.sql`).
`bloom_admin` gets FOR ALL; `bloom_agent`/`bloom_user` get SELECT-only. No role
gets DELETE.

LOCAL ONLY: the `pg_conn` fixture connects as `supabase_admin` (BYPASSRLS) and
every test rolls back, leaving the database untouched. Tests seed the
`cyl-intermediates` bucket row themselves (`ON CONFLICT DO NOTHING`) so they
correctly show RED before the migration exists — `storage.objects.bucket_id`
FKs to `storage.buckets.id`, so a bucket row must exist before any object row
can reference it, independent of whether the RLS policies (the thing under
test) have been created yet.

Runs in CI's `compose-health-check` job after migrations are applied
(`uv run --extra test pytest tests/integration/ -v`).
"""

import re
from pathlib import Path

import pytest

# Skip the whole module if psycopg isn't available (matches the change-A test).
psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
BUCKET = "cyl-intermediates"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _seed_bucket(cur):
    """Ensure the bucket row exists (as supabase_admin, BYPASSRLS) so an
    object INSERT's FK to storage.buckets doesn't fail regardless of whether
    migration 1.2 has been applied yet."""
    cur.execute(
        "INSERT INTO storage.buckets (id, name) VALUES (%s, %s) "
        "ON CONFLICT (id) DO NOTHING",
        (BUCKET, BUCKET),
    )


def _insert_object(cur, name: str):
    cur.execute(
        "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s) RETURNING id",
        (BUCKET, name),
    )
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# RLS enforcement — exercise the real roles with SET LOCAL ROLE.
# (pg_conn is supabase_admin / BYPASSRLS, so catalog rows alone prove nothing.)
# --------------------------------------------------------------------------- #


def test_bloom_roles_are_not_bypassrls(pg_conn):
    # Guards the SET LOCAL ROLE tests below: if a role were BYPASSRLS, the
    # write-denial assertions would false-pass.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT rolname, rolbypassrls FROM pg_roles "
            "WHERE rolname IN ('bloom_user', 'bloom_agent', 'bloom_writer', 'bloom_workflows')"
        )
        bypass = {row[0]: row[1] for row in cur.fetchall()}
    assert bypass == {
        "bloom_user": False,
        "bloom_agent": False,
        "bloom_writer": False,
        "bloom_workflows": False,
    }
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["bloom_writer", "bloom_workflows"])
def test_writer_roles_can_upload_and_read_back(pg_conn, role):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)  # as supabase_admin
        cur.execute(f"SET LOCAL ROLE {role}")
        oid = _insert_object(cur, f"{role}-upload-test.slp")
        cur.execute(
            "SELECT name FROM storage.objects WHERE id = %s AND bucket_id = %s",
            (oid, BUCKET),
        )
        assert cur.fetchone() == (f"{role}-upload-test.slp",), (
            f"role {role} could not read back its own upload — "
            "SELECT policy missing for this bucket"
        )
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["bloom_agent", "bloom_user"])
def test_readonly_roles_can_select(pg_conn, role):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        _insert_object(cur, "seed-for-readonly-select.slp")  # as supabase_admin
        cur.execute(f"SET LOCAL ROLE {role}")
        cur.execute(
            "SELECT count(*) FROM storage.objects WHERE bucket_id = %s", (BUCKET,)
        )
        assert cur.fetchone()[0] is not None, (
            f"role {role} could not read storage.objects for {BUCKET} — "
            "SELECT policy missing"
        )
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["bloom_agent", "bloom_user"])
def test_readonly_roles_cannot_insert(pg_conn, role):
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)  # as supabase_admin
        cur.execute(f"SET LOCAL ROLE {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _insert_object(cur, f"{role}-should-not-insert.slp")
    pg_conn.rollback()


@pytest.mark.parametrize(
    "role", ["bloom_agent", "bloom_user", "bloom_writer", "bloom_workflows"]
)
def test_no_non_admin_role_can_delete(pg_conn, role):
    """No non-admin role can delete a cyl-intermediates object — but the
    mechanism differs by role. bloom_agent/bloom_user/bloom_workflows have no
    DELETE grant on storage.objects at all, so the statement raises
    InsufficientPrivilege outright. bloom_writer inherits `authenticated`'s
    table-level DELETE grant, so the statement doesn't raise — but RLS has no
    permissive DELETE policy for this bucket, so Postgres just silently
    filters it to zero affected rows (RLS on DELETE/UPDATE filters rows
    rather than erroring, unlike INSERT's WITH CHECK). Assert the actual
    invariant that holds either way: the row survives.
    """
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        oid = _insert_object(cur, f"{role}-should-not-delete.slp")  # as supabase_admin
        cur.execute(f"SET LOCAL ROLE {role}")
        try:
            cur.execute("DELETE FROM storage.objects WHERE id = %s", (oid,))
        except psycopg.errors.InsufficientPrivilege:
            pg_conn.rollback()
            return
        assert cur.rowcount == 0, f"role {role} deleted a row it should not have access to"
    pg_conn.rollback()


def test_admin_has_full_access(pg_conn):
    # Deliberately exercises INSERT/SELECT/DELETE only, not UPDATE: renaming
    # an object fires Supabase Storage's internal prefix-maintenance trigger,
    # which needs a storage.prefixes grant bloom_admin doesn't have today —
    # a pre-existing gap across every bucket (confirmed: bloom_admin has zero
    # grants on storage.prefixes, unlike bloom_writer/authenticated/
    # service_role), not something this migration introduces or is scoped to
    # fix. INSERT/DELETE don't touch that trigger path.
    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        cur.execute("SET LOCAL ROLE bloom_admin")
        oid = _insert_object(cur, "admin-full-access-test.slp")
        cur.execute("SELECT 1 FROM storage.objects WHERE id = %s", (oid,))
        assert cur.fetchone() == (1,)
        cur.execute("DELETE FROM storage.objects WHERE id = %s", (oid,))
        assert cur.rowcount == 1
        cur.execute("SELECT 1 FROM storage.objects WHERE id = %s", (oid,))
        assert cur.fetchone() is None
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Drift detector — the expected explicit (role, cmd) policy set for this
# bucket. (Some roles — bloom_admin/bloom_agent/bloom_writer — already have
# blanket, bucket-agnostic storage.objects policies from
# 20260506000001_bloom_role_rls_policies.sql /
# 20260519130000_add_bloom_writer_role.sql; this migration still writes
# explicit bucket-scoped policies for all five roles anyway, matching
# cyl_scan_intermediates's fully-explicit modern per-role style.)
# --------------------------------------------------------------------------- #


def test_expected_bucket_scoped_policy_set(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT policyname, cmd, roles::text, qual, with_check "
            "FROM pg_policies WHERE schemaname = 'storage' AND tablename = 'objects'"
        )
        rows = cur.fetchall()
    pg_conn.rollback()

    pairs = set()
    for policyname, cmd, roles_text, qual, with_check in rows:
        text = f"{policyname} {qual or ''} {with_check or ''}"
        if BUCKET not in text:
            continue
        for role in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", roles_text):
            pairs.add((role, cmd))

    expected = {
        ("bloom_admin", "ALL"),
        ("bloom_agent", "SELECT"),
        ("bloom_user", "SELECT"),
        ("bloom_writer", "SELECT"),
        ("bloom_writer", "INSERT"),
        ("bloom_writer", "UPDATE"),
        ("bloom_workflows", "SELECT"),
        ("bloom_workflows", "INSERT"),
        ("bloom_workflows", "UPDATE"),
    }
    assert expected <= pairs, f"missing expected bucket-scoped policies: {expected - pairs}"

    forbidden = {
        (role, cmd)
        for (role, cmd) in pairs
        if role in {"bloom_user", "bloom_agent", "bloom_writer", "bloom_workflows"}
        and cmd in {"DELETE", "ALL"}
    }
    assert not forbidden, f"non-admin roles must not have DELETE/ALL policies: {forbidden}"


# --------------------------------------------------------------------------- #
# Rollback — storage.objects.bucket_id FKs to storage.buckets.id with no
# cascade, so a non-empty bucket must not be silently droppable.
# --------------------------------------------------------------------------- #


def _find_rollback_sql() -> Path | None:
    matches = sorted(
        (REPO_ROOT / "supabase" / "rollbacks").glob(
            "*_create_cyl_intermediates_bucket_rollback.sql"
        )
    )
    return matches[-1] if matches else None


def _strip_txn_wrapper(text: str) -> str:
    return "\n".join(
        line
        for line in text.splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def test_rollback_script_drops_the_bucket_when_empty(pg_conn):
    rollback_path = _find_rollback_sql()
    if rollback_path is None:
        pytest.skip("rollback script not written yet")

    with pg_conn.cursor() as cur:
        _seed_bucket(cur)  # as supabase_admin, empty (no objects inserted)
        cur.execute(_strip_txn_wrapper(rollback_path.read_text()))
        cur.execute(
            "SELECT 1 FROM storage.buckets WHERE id = %s", (BUCKET,)
        )
        assert cur.fetchone() is None, "rollback did not drop an empty cyl-intermediates bucket"
    pg_conn.rollback()  # restore the schema — leave the DB untouched


def test_rollback_script_refuses_a_non_empty_bucket(pg_conn):
    rollback_path = _find_rollback_sql()
    if rollback_path is None:
        pytest.skip("rollback script not written yet")

    with pg_conn.cursor() as cur:
        _seed_bucket(cur)
        _insert_object(cur, "still-here.slp")  # as supabase_admin
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(_strip_txn_wrapper(rollback_path.read_text()))
    pg_conn.rollback()
    with pg_conn.cursor() as cur:
        # The aborted rollback must not have removed the bucket or its policies.
        cur.execute("SELECT 1 FROM storage.buckets WHERE id = %s", (BUCKET,))
        assert cur.fetchone() is not None, (
            "bucket should still exist after a refused (non-empty) rollback attempt"
        )
    pg_conn.rollback()
