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
BUCKET = "graviscan-images"
OBJECT = "1/wave-1/PLATE-RLS.tif"


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
        (scan_id, OBJECT),
    )
    return scan_id


def test_the_role_is_not_bypassrls(pg_conn):
    """The premise of every other test here. A BYPASSRLS role reads everything
    regardless of policy, so without this they would pass with no policy at all."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = %s", (ROLE,)
        )
        row = cur.fetchone()
        assert row is not None, f"{ROLE} does not exist"
        # A superuser bypasses RLS whatever rolbypassrls says, so both must be false.
        assert row == (False, False), f"{ROLE} bypasses RLS; these tests would prove nothing"
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
    assert rows[0][1] == OBJECT
    pg_conn.rollback()


@pytest.mark.parametrize("table", ["gravi_scans", "gravi_images", "gravi_scan_sessions", "gravi_plate_videos"])
@pytest.mark.parametrize("verb", ["INSERT", "UPDATE", "DELETE"])
def test_cannot_write_either_table(pg_conn, table, verb):
    """Read access only. A write must be refused, not silently accepted.

    42501 covers both a missing grant and a missing RLS policy, so this does not
    say which refused it — `test_holds_select_and_nothing_else` is what pins the
    grant. One statement per test: the first error aborts the transaction, so a
    second write in the same one would raise InFailedSqlTransaction and pass for
    the wrong reason.
    """
    statement = {
        "INSERT": f"INSERT INTO {table} (id) VALUES (%s)",
        "UPDATE": f"UPDATE {table} SET id = id WHERE id = %s",
        "DELETE": f"DELETE FROM {table} WHERE id = %s",
    }[verb]

    with pg_conn.cursor() as cur:
        scan_id = _seed_plate(cur)
        cur.execute(f"SET LOCAL ROLE {ROLE}")

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(statement, (scan_id,))

    pg_conn.rollback()


@pytest.mark.parametrize("table", ["gravi_scans", "gravi_images", "gravi_scan_sessions", "gravi_plate_videos"])
def test_holds_select_and_nothing_else(pg_conn, table):
    """Privilege-level check, so a write refusal cannot be mistaken for an RLS
    policy that happens to match no rows."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_table_privilege(%s, %s, 'SELECT')", (ROLE, f"public.{table}")
        )
        assert cur.fetchone()[0] is True, f"{ROLE} cannot SELECT {table}"

        # Column-grantable privileges are checked per column: a grant like
        # INSERT (scan_id, path, frames) -- which this role already holds on
        # cyl_scan_videos -- leaves has_table_privilege false.
        for priv in ("INSERT", "UPDATE", "REFERENCES"):
            cur.execute(
                "SELECT has_any_column_privilege(%s, %s, %s)",
                (ROLE, f"public.{table}", priv),
            )
            assert cur.fetchone()[0] is False, f"{ROLE} unexpectedly holds {priv} on {table}"

        # DELETE and TRUNCATE cannot be granted per column.
        for priv in ("DELETE", "TRUNCATE"):
            cur.execute(
                "SELECT has_table_privilege(%s, %s, %s)", (ROLE, f"public.{table}", priv)
            )
            assert cur.fetchone()[0] is False, f"{ROLE} unexpectedly holds {priv} on {table}"
    pg_conn.rollback()


def test_reads_graviscan_images_objects_and_not_another_bucket(pg_conn):
    """Reads a real storage row as the role, rather than reading the policy back
    out of the catalogue — a policy that exists still proves nothing about what
    it returns.

    The control bucket is a throwaway rather than a real one: `scrna` carries a
    `roles=public` policy, and `images` and `videos` each have a policy for this
    role, so none of them can show that the graviscan grant is scoped.
    """
    with pg_conn.cursor() as cur:
        for bucket in (BUCKET, "zzz-rls-control"):
            cur.execute(
                "INSERT INTO storage.buckets (id, name) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (bucket, bucket),
            )
            cur.execute(
                "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s)",
                (bucket, OBJECT),
            )

        cur.execute(f"SET LOCAL ROLE {ROLE}")
        cur.execute(
            "SELECT bucket_id FROM storage.objects WHERE name = %s",
            (OBJECT,),
        )
        visible = {r[0] for r in cur.fetchall()}

    assert BUCKET in visible, "the role cannot read the bucket it was granted"
    assert "zzz-rls-control" not in visible, f"the grant is not scoped to one bucket: {visible}"
    pg_conn.rollback()


def test_cannot_write_an_object_into_graviscan_images(pg_conn):
    """The role holds table-level INSERT and UPDATE on storage.objects, so the
    only thing keeping it out of this bucket is the absence of a policy.

    Attempts the write rather than enumerating policies: a catalogue query has
    to guess which policy shapes count, and misses an unscoped `FOR ALL` one,
    or one granted `TO public`, or one whose predicate never names the bucket.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO storage.buckets (id, name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (BUCKET, BUCKET),
        )
        cur.execute(f"SET LOCAL ROLE {ROLE}")

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s)",
                (BUCKET, "written-by-the-role.mp4"),
            )

    pg_conn.rollback()
