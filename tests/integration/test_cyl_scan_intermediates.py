"""
Integration tests for change `add-cyl-scan-intermediates` (change C, #296).

`cyl_scan_intermediates` is a per-scan artifact-pointer table: one row per
pipeline artifact file (today one SLEAP `.slp` per root type per scan). It carries
`source_id` (FK -> cyl_trait_sources), `scan_id` (FK -> cyl_scans), `kind` and
`root_type` (strict CHECK vocabularies), `s3_location` / `box_link` (the dual
pointer, at least one required) and `checksum` / `file_size` (integrity).

These tests assert: the columns and types; the two FKs (by contype/confrelid);
the at-least-one-location CHECK; the strict kind/root_type vocabularies; the
UNIQUE(source_id, scan_id, kind, root_type) idempotency key; role-based RLS
ENFORCEMENT via `SET LOCAL ROLE` plus a pg_policies drift detector; that the
migration is additive (FK parents unchanged); and that the rollback script drops
the table.

NOTE (changes D/E, `add-cyl-writeback-rpc`): the table is now locked to RPC-only
writes — `bloom_writer`'s direct INSERT/UPDATE policies were dropped, so every
role except `bloom_admin` is read-only at the table; all writes go through the
`insert_cyl_result_envelope` RPC (covered by `test_cyl_writeback_rpc.py`).

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT and
mutates nothing — every test rolls back, leaving the database untouched. The fixture
connects as `supabase_admin`, which is BYPASSRLS, so the RLS tests use `SET LOCAL
ROLE` to exercise the real bloom_* roles (catalog introspection alone proves nothing).

Runs in CI's `compose-health-check` job after migrations are applied
(`uv run --extra test pytest tests/integration/ -v`).
"""

import re
from pathlib import Path

import pytest

# Skip the whole module if psycopg isn't available (matches the change-A test).
psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
TABLE = "cyl_scan_intermediates"


# --------------------------------------------------------------------------- #
# Introspection helpers
# --------------------------------------------------------------------------- #


def _column_type(cur, column: str) -> str | None:
    cur.execute(
        """
        SELECT data_type
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
        """,
        (TABLE, column),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _foreign_keys(cur, table: str) -> set[tuple[str, str]]:
    """Return {(local_column, referenced_table)} for every FK on `table`, keyed by
    contype='f' / confrelid — not by constraint name."""
    cur.execute(
        """
        SELECT a.attname, cf.relname
          FROM pg_constraint c
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
          JOIN pg_class cf     ON cf.oid = c.confrelid
         WHERE c.conrelid = %s::regclass AND c.contype = 'f'
        """,
        (f"public.{table}",),
    )
    return {(row[0], row[1]) for row in cur.fetchall()}


def _check_defs(cur, table: str) -> list[str]:
    cur.execute(
        """
        SELECT pg_get_constraintdef(c.oid)
          FROM pg_constraint c
         WHERE c.conrelid = %s::regclass AND c.contype = 'c'
        """,
        (f"public.{table}",),
    )
    return [row[0] for row in cur.fetchall()]


def _seed_parents(cur):
    """Seed FK parents inside the caller's uncommitted txn (rolled back by the test).
    Two trait-source rows (for the history test) and one scan. cyl_scans has only
    nullable FK columns, so DEFAULT VALUES needs no deeper seeding."""
    cur.execute("INSERT INTO cyl_trait_sources (name) VALUES ('c-src-a') RETURNING id")
    source_a = cur.fetchone()[0]
    cur.execute("INSERT INTO cyl_trait_sources (name) VALUES ('c-src-b') RETURNING id")
    source_b = cur.fetchone()[0]
    cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
    scan_id = cur.fetchone()[0]
    return source_a, source_b, scan_id


def _insert(
    cur,
    source_id,
    scan_id,
    *,
    kind="predictions_slp",
    root_type="primary",
    s3_location="s3://bloom/preds/a.slp",
    box_link=None,
    checksum=None,
    file_size=None,
):
    cur.execute(
        f"""
        INSERT INTO {TABLE}
            (source_id, scan_id, kind, root_type, s3_location, box_link, checksum, file_size)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (source_id, scan_id, kind, root_type, s3_location, box_link, checksum, file_size),
    )
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# Columns / types
# --------------------------------------------------------------------------- #


def test_columns_and_types(pg_conn):
    with pg_conn.cursor() as cur:
        assert _column_type(cur, "source_id") == "bigint"
        assert _column_type(cur, "scan_id") == "bigint"
        assert _column_type(cur, "kind") == "text"
        assert _column_type(cur, "root_type") == "text"
        assert _column_type(cur, "s3_location") == "text"
        assert _column_type(cur, "box_link") == "text"
        assert _column_type(cur, "checksum") == "text"
        assert _column_type(cur, "file_size") == "bigint"
    pg_conn.rollback()


def test_fully_specified_row_round_trips(pg_conn):
    with pg_conn.cursor() as cur:
        source_a, _, scan_id = _seed_parents(cur)
        rid = _insert(
            cur, source_a, scan_id,
            s3_location="s3://bloom/preds/primary.slp",
            box_link="https://salkinstitute.box.com/s/abc",
            checksum="sha256:deadbeef",
            file_size=12345,
        )
        cur.execute(
            f"SELECT kind, root_type, s3_location, box_link, checksum, file_size "
            f"FROM {TABLE} WHERE id = %s",
            (rid,),
        )
        assert cur.fetchone() == (
            "predictions_slp", "primary",
            "s3://bloom/preds/primary.slp",
            "https://salkinstitute.box.com/s/abc",
            "sha256:deadbeef", 12345,
        )
    pg_conn.rollback()


def test_optional_integrity_columns_may_be_null(pg_conn):
    with pg_conn.cursor() as cur:
        source_a, _, scan_id = _seed_parents(cur)
        rid = _insert(cur, source_a, scan_id, checksum=None, file_size=None)
        cur.execute(f"SELECT checksum, file_size FROM {TABLE} WHERE id = %s", (rid,))
        assert cur.fetchone() == (None, None)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Foreign keys
# --------------------------------------------------------------------------- #


def test_foreign_keys_reference_provenance_and_scan(pg_conn):
    with pg_conn.cursor() as cur:
        fks = _foreign_keys(cur, TABLE)
    assert ("source_id", "cyl_trait_sources") in fks
    assert ("scan_id", "cyl_scans") in fks
    pg_conn.rollback()


def test_missing_scan_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        source_a, _, _ = _seed_parents(cur)
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _insert(cur, source_a, 2_000_000_001)  # scan_id absent from cyl_scans
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# At-least-one-location CHECK
# --------------------------------------------------------------------------- #


def test_both_locations_null_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        source_a, _, scan_id = _seed_parents(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(cur, source_a, scan_id, s3_location=None, box_link=None)
    pg_conn.rollback()


def test_box_only_location_is_accepted(pg_conn):
    with pg_conn.cursor() as cur:
        source_a, _, scan_id = _seed_parents(cur)
        rid = _insert(
            cur, source_a, scan_id,
            s3_location=None,
            box_link="https://salkinstitute.box.com/s/only-box",
        )
        cur.execute(f"SELECT s3_location, box_link FROM {TABLE} WHERE id = %s", (rid,))
        assert cur.fetchone() == (None, "https://salkinstitute.box.com/s/only-box")
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Strict kind / root_type vocabularies
# --------------------------------------------------------------------------- #


def test_unknown_kind_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        source_a, _, scan_id = _seed_parents(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(cur, source_a, scan_id, kind="h5")
    pg_conn.rollback()


def test_unknown_root_type_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        source_a, _, scan_id = _seed_parents(cur)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(cur, source_a, scan_id, root_type="seminal")
    pg_conn.rollback()


@pytest.mark.parametrize("root_type", ["primary", "lateral", "crown"])
def test_each_vocabulary_root_type_is_accepted(pg_conn, root_type):
    with pg_conn.cursor() as cur:
        source_a, _, scan_id = _seed_parents(cur)
        rid = _insert(cur, source_a, scan_id, root_type=root_type)
        cur.execute(f"SELECT root_type FROM {TABLE} WHERE id = %s", (rid,))
        assert cur.fetchone()[0] == root_type
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# UNIQUE(source_id, scan_id, kind, root_type) — per-run idempotency
# --------------------------------------------------------------------------- #


def test_duplicate_run_scan_kind_root_type_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        source_a, _, scan_id = _seed_parents(cur)
        _insert(cur, source_a, scan_id, kind="predictions_slp", root_type="primary")
        with pytest.raises(psycopg.errors.UniqueViolation):
            _insert(cur, source_a, scan_id, kind="predictions_slp", root_type="primary",
                    s3_location="s3://bloom/preds/dup.slp")
    pg_conn.rollback()


def test_same_artifact_from_a_different_run_is_permitted(pg_conn):
    # Same (scan, kind, root_type) but a different source_id (a re-run) — both kept.
    with pg_conn.cursor() as cur:
        source_a, source_b, scan_id = _seed_parents(cur)
        _insert(cur, source_a, scan_id, kind="predictions_slp", root_type="primary")
        rid_b = _insert(cur, source_b, scan_id, kind="predictions_slp", root_type="primary",
                        s3_location="s3://bloom/preds/run-b.slp")
        cur.execute(f"SELECT count(*) FROM {TABLE} WHERE scan_id = %s", (scan_id,))
        assert cur.fetchone()[0] == 2
        assert rid_b is not None
    pg_conn.rollback()


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
            "WHERE rolname IN ('bloom_user', 'bloom_agent', 'bloom_writer')"
        )
        bypass = {row[0]: row[1] for row in cur.fetchall()}
    assert bypass == {"bloom_user": False, "bloom_agent": False, "bloom_writer": False}
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["bloom_admin", "bloom_agent", "bloom_user", "bloom_writer"])
def test_every_role_can_read(pg_conn, role):
    with pg_conn.cursor() as cur:
        cur.execute(f"SET LOCAL ROLE {role}")
        cur.execute(f"SELECT count(*) FROM public.{TABLE}")
        assert cur.fetchone()[0] is not None, (
            f"role {role} could not read public.{TABLE} — GRANT or SELECT policy missing"
        )
        cur.execute("RESET ROLE")
    pg_conn.rollback()


def test_writer_cannot_insert_or_update_directly(pg_conn):
    # Changes D/E: the table is locked to RPC-only writes — bloom_writer's direct
    # INSERT/UPDATE policies were dropped, so a direct write is now denied by RLS.
    # (Writes go through insert_cyl_result_envelope; see test_cyl_writeback_rpc.py.)
    with pg_conn.cursor() as cur:
        source_a, _, scan_id = _seed_parents(cur)  # as supabase_admin
        cur.execute("SET LOCAL ROLE bloom_writer")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _insert(cur, source_a, scan_id)
        # the denied INSERT aborts the txn; pg_conn.rollback() resets the LOCAL ROLE
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent"])
def test_read_only_roles_cannot_insert(pg_conn, role):
    with pg_conn.cursor() as cur:
        source_a, _, scan_id = _seed_parents(cur)  # as supabase_admin
        cur.execute(f"SET LOCAL ROLE {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _insert(cur, source_a, scan_id)
    pg_conn.rollback()


def test_expected_policy_set_with_no_readonly_write_policy(pg_conn):
    """Drift detector. RLS enabled; exactly the expected (role, cmd) policy pairs;
    and (changes D/E) NO write policy for any non-admin role — bloom_writer is now
    read-only at the table too, since writes go through the write-back RPC. Read-only
    posture is enforced by the absence of write policies (the standing default GRANTs
    are permissive, so RLS, not the GRANT, is the write gate)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"public.{TABLE}",),
        )
        assert cur.fetchone()[0] is True, "row-level security is not enabled"

        cur.execute(
            "SELECT policyname, cmd, roles::text FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = %s",
            (TABLE,),
        )
        pairs = set()
        for _, cmd, roles_text in cur.fetchall():
            for role in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", roles_text):
                pairs.add((role, cmd))
    pg_conn.rollback()

    expected = {
        ("bloom_admin", "ALL"),
        ("bloom_agent", "SELECT"),
        ("bloom_user", "SELECT"),
        ("bloom_writer", "SELECT"),
    }
    assert expected <= pairs, f"missing expected policies: {expected - pairs}"

    forbidden = {
        (role, cmd)
        for (role, cmd) in pairs
        if role in {"bloom_user", "bloom_agent", "bloom_writer"}
        and cmd in {"INSERT", "UPDATE", "DELETE", "ALL"}
    }
    assert not forbidden, f"non-admin roles must not have write policies: {forbidden}"


# --------------------------------------------------------------------------- #
# Additive migration + rollback
# --------------------------------------------------------------------------- #


def test_migration_is_additive_fk_parents_unchanged(pg_conn):
    # The new table must not alter the FK-parent tables it references.
    def _has_column(cur, table, column):
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = %s",
            (table, column),
        )
        return cur.fetchone() is not None

    with pg_conn.cursor() as cur:
        assert _has_column(cur, "cyl_trait_sources", "metadata")
        assert _has_column(cur, "cyl_trait_sources", "idempotency_key")
        assert _has_column(cur, "cyl_scans", "id")
        assert _has_column(cur, "cyl_scans", "plant_id")
    pg_conn.rollback()


def _find_rollback_sql() -> Path | None:
    matches = sorted(
        (REPO_ROOT / "supabase" / "rollbacks").glob(
            "*_create_cyl_scan_intermediates_rollback.sql"
        )
    )
    return matches[-1] if matches else None


def test_rollback_script_drops_the_table(pg_conn):
    """CI only rolls forward, so apply the rollback body inside the fixture's
    uncommitted transaction, assert the table is gone, then ROLLBACK so the schema
    (and other tests) are unaffected."""
    rollback_path = _find_rollback_sql()
    if rollback_path is None:
        pytest.skip("rollback script not written yet")

    # Strip the BEGIN;/COMMIT; wrapper (reuse change A's exact, CRLF-safe pattern).
    body = "\n".join(
        line
        for line in rollback_path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )
    with pg_conn.cursor() as cur:
        cur.execute(body)
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (TABLE,),
        )
        assert cur.fetchone() is None, "rollback did not drop cyl_scan_intermediates"
    pg_conn.rollback()  # restore the schema — leave the DB untouched
