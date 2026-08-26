"""What `bloom_workflows` can and cannot reach on the gravi tables.

Covers 20260825210000_workflows_read_gravi.sql: the plate time-lapse endpoint
reads a plate's captures and their image paths, and nothing else.

Both tables have RLS on, so a grant without a policy returns no rows rather
than failing — a test that only checked the grant would pass either way. These
read actual rows as the role.

Uses the `pg_conn` fixture (connects as `supabase_admin`). Every role switch and
insert is rolled back, so no state leaks.
"""

import psycopg
import pytest

ROLE = "bloom_workflows"


def _seed_plate(cur):
    """One experiment, one capture, one image. Returns the scan id."""
    cur.execute(
        "INSERT INTO gravi_experiments (name) VALUES ('rls-probe') RETURNING id"
    )
    experiment_id = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO gravi_scans
            (experiment_id, plate_id, wave_number, capture_date,
             grid_mode, plate_index, resolution)
        VALUES (%s, 'PLATE-RLS', 1, now(), 'single', '00', 600)
        RETURNING id
        """,
        (experiment_id,),
    )
    scan_id = cur.fetchone()[0]

    cur.execute(
        "INSERT INTO gravi_images (scan_id, object_path) VALUES (%s, %s)",
        (scan_id, "1/wave-1/PLATE-RLS.tif"),
    )
    return scan_id


def test_the_role_is_not_bypassrls(pg_conn):
    """The premise of every other test here. A BYPASSRLS role reads everything
    regardless of policy, so without this they would pass with no policy at all."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = %s", (ROLE,))
        row = cur.fetchone()
        assert row is not None, f"{ROLE} does not exist"
        assert row[0] is False, f"{ROLE} is BYPASSRLS; these tests would prove nothing"
    pg_conn.rollback()


def test_reads_a_plates_captures_and_image_paths(pg_conn):
    """The frame query the encoder makes: captures joined to their images."""
    with pg_conn.cursor() as cur:
        scan_id = _seed_plate(cur)

        cur.execute(f"SET LOCAL ROLE {ROLE}")
        cur.execute(
            """
            SELECT s.capture_date, i.object_path
            FROM gravi_scans s
            JOIN gravi_images i ON i.scan_id = s.id
            WHERE s.id = %s
            """,
            (scan_id,),
        )
        rows = cur.fetchall()

    assert len(rows) == 1, "the role sees no rows — grant without a policy reads empty"
    assert rows[0][1] == "1/wave-1/PLATE-RLS.tif"
    pg_conn.rollback()


@pytest.mark.parametrize("table", ["gravi_scans", "gravi_images"])
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_cannot_write_either_table(pg_conn, table, verb):
    """Read access only. A write must fail at the privilege check, not silently.

    One statement per test: the first error aborts the transaction, so a second
    write in the same one would raise InFailedSqlTransaction and pass for the
    wrong reason.
    """
    statement = {
        "UPDATE": f"UPDATE {table} SET id = id WHERE id = %s",
        "DELETE": f"DELETE FROM {table} WHERE id = %s",
    }[verb]

    with pg_conn.cursor() as cur:
        scan_id = _seed_plate(cur)
        cur.execute(f"SET LOCAL ROLE {ROLE}")

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(statement, (scan_id,))

    pg_conn.rollback()


@pytest.mark.parametrize("table", ["gravi_scans", "gravi_images"])
def test_holds_select_and_nothing_else(pg_conn, table):
    """Privilege-level check, so a write refusal cannot be mistaken for an RLS
    policy that happens to match no rows."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT')", (ROLE, f"public.{table}")
        )
        assert cur.fetchone()[0] is True, f"{ROLE} cannot SELECT {table}"

        for priv in ("INSERT", "UPDATE", "DELETE"):
            cur.execute(
                "SELECT has_table_privilege(%s, %s, %s)", (ROLE, f"public.{table}", priv)
            )
            assert cur.fetchone()[0] is False, f"{ROLE} unexpectedly holds {priv} on {table}"
    pg_conn.rollback()


def test_reads_the_graviscan_images_bucket_and_no_other(pg_conn):
    """The policy names one bucket. A policy without the bucket_id predicate
    would open every bucket in storage.objects to this role."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT qual
            FROM pg_policies
            WHERE schemaname = 'storage'
              AND tablename = 'objects'
              AND policyname = 'workflows_read_graviscan_images'
            """
        )
        row = cur.fetchone()
        assert row is not None, "the graviscan-images read policy is missing"
        assert "graviscan-images" in row[0], f"policy is not scoped to one bucket: {row[0]}"

        cur.execute(
            """
            SELECT count(*)
            FROM pg_policies
            WHERE schemaname = 'storage'
              AND tablename = 'objects'
              AND %s = ANY(roles)
              AND cmd <> 'SELECT'
              AND qual LIKE '%%graviscan%%'
            """,
            (ROLE,),
        )
        assert cur.fetchone()[0] == 0, "read access only — no write policy on graviscan yet"
    pg_conn.rollback()
