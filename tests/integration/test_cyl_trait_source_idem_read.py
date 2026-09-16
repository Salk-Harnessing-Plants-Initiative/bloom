"""What `bloom_workflows` can and cannot reach on `cyl_trait_sources.idempotency_key`.

Covers 20260916120000_grant_workflows_read_cyl_trait_source_idem.sql, which exists so bloomctl
can tell whether a delivery was already ingested before uploading its blobs
(talmolab/sleap-roots-pipeline#76).

Why this test and not just a `column_privileges` query: the ACL row existing and the query
actually running are different claims, and the gap between them has bitten this project before.
`tests/unit/test_cyl_scan_videos_grants.py` records a column-grant/PostgREST mismatch whose 42501
was swallowed by a caller's `except`, which is "how production came to hold zero rows against
84,748 stored videos". `source_already_ingested` fails open by design, so if this grant is wrong
the only symptom is that the fix silently does nothing. This test is what makes that loud.

Postgres requires SELECT on every column a query REFERENCES, including in WHERE — not just the
select-list — so filtering on `idempotency_key` needs its own grant even though `id` was already
readable. That is the specific thing being pinned.

Uses the `pg_conn` fixture (connects as `supabase_admin`). Every role switch and insert is
rolled back, so no state leaks.
"""

import psycopg
import pytest

ROLE = "bloom_workflows"
IDEM = "idem-integration-probe-0000000000000000000000000000000000000000"


def _seed_source(cur):
    """One cyl_trait_sources row carrying a known idempotency_key. Returns its id."""
    cur.execute(
        """
        INSERT INTO cyl_trait_sources (name, metadata, idempotency_key)
        VALUES ('integration-probe', '{}'::jsonb, %s)
        RETURNING id
        """,
        (IDEM,),
    )
    return cur.fetchone()[0]


def test_the_role_is_not_bypassrls(pg_conn):
    """The premise of every other test here. A BYPASSRLS role reads everything regardless of
    policy, so without this they would pass with no policy at all."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = %s", (ROLE,))
        row = cur.fetchone()
        assert row is not None, f"{ROLE} does not exist"
        assert row == (False, False), f"{ROLE} bypasses RLS; these tests would prove nothing"
    pg_conn.rollback()


def test_the_role_can_filter_on_idempotency_key(pg_conn):
    """The exact query `source_already_ingested` issues."""
    with pg_conn.cursor() as cur:
        source_id = _seed_source(cur)

        cur.execute(f"SET LOCAL ROLE {ROLE}")
        cur.execute("SELECT id FROM cyl_trait_sources WHERE idempotency_key = %s", (IDEM,))
        rows = cur.fetchall()

    assert rows == [(source_id,)], (
        "the role cannot read its own row back — a grant without a policy returns empty, "
        "and the gate would then fail open forever"
    )
    pg_conn.rollback()


def test_an_absent_key_returns_no_rows_rather_than_raising(pg_conn):
    """The "not yet ingested" answer must be an empty result, not an error — an error would be
    swallowed by the fail-open catch and is indistinguishable from a missing grant."""
    with pg_conn.cursor() as cur:
        _seed_source(cur)

        cur.execute(f"SET LOCAL ROLE {ROLE}")
        cur.execute(
            "SELECT id FROM cyl_trait_sources WHERE idempotency_key = %s", ("no-such-key",)
        )
        assert cur.fetchall() == []
    pg_conn.rollback()


def test_other_columns_remain_ungranted(pg_conn):
    """`name` is the only column on this table the role is not granted, and it must stay that
    way. A column-less GRANT SELECT would quietly reach everything while still reading like a
    tightening in review."""
    with pg_conn.cursor() as cur:
        cur.execute(f"SET LOCAL ROLE {ROLE}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("SELECT name FROM cyl_trait_sources LIMIT 1")
    pg_conn.rollback()


def test_the_granted_column_set_is_exactly_the_three_expected(pg_conn):
    """Pins the whole grant surface rather than one column at a time, so a future widening --
    or a new column quietly added to the grant -- fails here instead of going unnoticed.

    Deliberately compares the full set: asserting only that `idempotency_key` is present would
    pass just as happily under a column-less `GRANT SELECT`, which is the thing worth catching.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.column_privileges
            WHERE table_schema = 'public'
              AND table_name = 'cyl_trait_sources'
              AND grantee = %s
              AND privilege_type = 'SELECT'
            """,
            (ROLE,),
        )
        granted = {row[0] for row in cur.fetchall()}

    assert granted == {"id", "metadata", "idempotency_key"}, (
        f"{ROLE}'s SELECT grant on cyl_trait_sources changed shape: {sorted(granted)}"
    )
    pg_conn.rollback()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO cyl_trait_sources (name, idempotency_key) VALUES ('x', 'y')",
        "UPDATE cyl_trait_sources SET name = 'x'",
        "DELETE FROM cyl_trait_sources",
    ],
)
def test_the_grant_confers_no_write_access(pg_conn, statement):
    """insert_cyl_result_envelope stays the sole writer of the trait tables."""
    with pg_conn.cursor() as cur:
        cur.execute(f"SET LOCAL ROLE {ROLE}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(statement)
    pg_conn.rollback()


def test_the_unique_index_backing_the_lookup_exists(pg_conn):
    """Deliberately an index-existence assertion, not an EXPLAIN one: CI's cyl_trait_sources is
    empty, so the planner seq-scans regardless and a plan assertion would prove nothing."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_indexes WHERE tablename = 'cyl_trait_sources' AND indexname = %s",
            ("cyl_trait_sources_idempotency_key_key",),
        )
        assert cur.fetchone() is not None
    pg_conn.rollback()
