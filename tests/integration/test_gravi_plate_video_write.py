"""What `bloom_workflows` can and cannot write for a plate's rendered video.

Covers 20260825220000_workflows_write_gravi_plate_video.sql. The unit tests read the
migration text; these run the statements, because the text says what was intended and
only the database says what is true.

Uses the `pg_conn` fixture (connects as `supabase_admin`). Every role switch and insert
is rolled back, so no state leaks.
"""

import psycopg
import pytest

ROLE = "bloom_workflows"
BUCKET = "graviscan-videos"
WRAPPER = "record_gravi_plate_video"


def _experiment(cur, name="write-probe"):
    cur.execute("INSERT INTO gravi_experiments (name) VALUES (%s) RETURNING id", (name,))
    return cur.fetchone()[0]


def _record(cur, experiment_id, *, plate="P1", wave=1, path="a.mp4", frames=100):
    cur.execute(
        f"SELECT {WRAPPER}(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (experiment_id, plate, wave, path, frames, 25, 4, 999, "hash"),
    )


def test_the_role_writes_the_record_through_the_wrapper(pg_conn):
    """The whole point: the role has no table grant, and the row still lands."""
    with pg_conn.cursor() as cur:
        experiment_id = _experiment(cur)
        cur.execute(f"SET LOCAL ROLE {ROLE}")
        _record(cur, experiment_id, path="first.mp4", frames=100)

        cur.execute("RESET ROLE")
        cur.execute(
            "SELECT object_path, frame_count, fps FROM gravi_plate_videos "
            "WHERE experiment_id = %s",
            (experiment_id,),
        )
        rows = cur.fetchall()

    assert rows == [("first.mp4", 100, 4)]
    pg_conn.rollback()


def test_a_second_render_of_a_plate_replaces_the_first(pg_conn):
    """One row per plate and wave — the conflict target has to match the unique index,
    or the second call raises instead of updating."""
    with pg_conn.cursor() as cur:
        experiment_id = _experiment(cur)
        cur.execute(f"SET LOCAL ROLE {ROLE}")
        _record(cur, experiment_id, path="first.mp4", frames=100)
        _record(cur, experiment_id, path="second.mp4", frames=200)

        cur.execute("RESET ROLE")
        cur.execute(
            "SELECT object_path, frame_count FROM gravi_plate_videos "
            "WHERE experiment_id = %s",
            (experiment_id,),
        )
        rows = cur.fetchall()

    assert rows == [("second.mp4", 200)]
    pg_conn.rollback()


def test_a_plate_with_no_wave_is_one_row_not_one_per_render(pg_conn):
    """NULL never equals NULL, so a plain column list in the conflict target would
    insert a new row every time. The index coalesces the wave to -1; so must this."""
    with pg_conn.cursor() as cur:
        experiment_id = _experiment(cur)
        cur.execute(f"SET LOCAL ROLE {ROLE}")
        _record(cur, experiment_id, wave=None, path="first.mp4")
        _record(cur, experiment_id, wave=None, path="second.mp4")

        cur.execute("RESET ROLE")
        cur.execute(
            "SELECT count(*) FROM gravi_plate_videos WHERE experiment_id = %s",
            (experiment_id,),
        )
        assert cur.fetchone()[0] == 1

    pg_conn.rollback()


def test_the_wrapper_never_repoints_a_row_at_another_plate(pg_conn):
    """The key columns are matched, not assigned. Two plates stay two rows."""
    with pg_conn.cursor() as cur:
        experiment_id = _experiment(cur)
        cur.execute(f"SET LOCAL ROLE {ROLE}")
        _record(cur, experiment_id, plate="P1")
        _record(cur, experiment_id, plate="P2")

        cur.execute("RESET ROLE")
        cur.execute(
            "SELECT plate_id FROM gravi_plate_videos WHERE experiment_id = %s "
            "ORDER BY plate_id",
            (experiment_id,),
        )
        assert [r[0] for r in cur.fetchall()] == ["P1", "P2"]

    pg_conn.rollback()


@pytest.mark.parametrize("verb", ["INSERT", "UPDATE"])
def test_the_role_cannot_write_the_table_directly(pg_conn, verb):
    """If it could, the wrapper would be decoration."""
    statement = {
        "INSERT": "INSERT INTO gravi_plate_videos "
        "(experiment_id, plate_id, object_path) VALUES (%s, 'P9', 'x.mp4')",
        "UPDATE": "UPDATE gravi_plate_videos SET object_path = 'x.mp4' "
        "WHERE experiment_id = %s",
    }[verb]

    with pg_conn.cursor() as cur:
        experiment_id = _experiment(cur)
        cur.execute(f"SET LOCAL ROLE {ROLE}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(statement, (experiment_id,))

    pg_conn.rollback()


@pytest.mark.parametrize("role", ["authenticated", "anon", "service_role", "bloom_user"])
def test_an_untrusted_role_cannot_call_the_wrapper(pg_conn, role):
    """It runs as its owner and is reachable over the API, so the default EXECUTE
    grants have to be gone.

    Asks whether the privilege is held rather than calling the function as that role:
    invoking it under a switched role took a backend down in testing, and a test that
    can restart the database would take every other test in the run with it.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
            (role, f"public.{WRAPPER}(bigint,text,int,text,int,int,int,bigint,text)"),
        )
        assert cur.fetchone()[0] is False, f"{role} can execute {WRAPPER}"
    pg_conn.rollback()


def test_the_service_role_that_renders_can_call_the_wrapper(pg_conn):
    """The companion to the test above: the revokes must not have taken the one
    grant that matters with them."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
            (ROLE, f"public.{WRAPPER}(bigint,text,int,text,int,int,int,bigint,text)"),
        )
        assert cur.fetchone()[0] is True, f"{ROLE} cannot execute {WRAPPER}"
    pg_conn.rollback()


def test_the_role_writes_the_videos_bucket_and_not_another(pg_conn):
    """Attempts the writes rather than reading policies back: a policy that exists
    still says nothing about what it permits.

    The control bucket is a throwaway — every real bucket either carries a policy for
    this role or one for `public`, so none of them can show the grant is scoped.
    """
    with pg_conn.cursor() as cur:
        for bucket in (BUCKET, "zzz-write-control"):
            cur.execute(
                "INSERT INTO storage.buckets (id, name) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (bucket, bucket),
            )
        cur.execute(f"SET LOCAL ROLE {ROLE}")

        cur.execute(
            "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s)",
            (BUCKET, "12/wave-1/P1.mp4"),
        )

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s)",
                ("zzz-write-control", "12/wave-1/P1.mp4"),
            )

    pg_conn.rollback()


def test_the_role_cannot_move_an_object_between_buckets(pg_conn):
    """Two UPDATE policies mean the role can write a row in either bucket, and
    policies are OR-ed — so RLS alone would let it set one bucket's row to the
    other's name, and RLS cannot compare the old row to the new one.

    Moving a cyl video into graviscan-videos would expose it to every signed-in
    user; moving a plate video out would leave the row pointing at a file that
    is no longer there. Withholding the bucket_id column is what stops both.
    """
    with pg_conn.cursor() as cur:
        for bucket in ("videos", BUCKET):
            cur.execute(
                "INSERT INTO storage.buckets (id, name) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (bucket, bucket),
            )
        cur.execute(
            "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s)",
            ("videos", "cyl-videos/999.mp4"),
        )
        cur.execute(f"SET LOCAL ROLE {ROLE}")

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "UPDATE storage.objects SET bucket_id = %s WHERE name = %s",
                (BUCKET, "cyl-videos/999.mp4"),
            )

    pg_conn.rollback()


def test_the_role_keeps_every_column_an_upload_writes(pg_conn):
    """The companion to the test above. Withholding bucket_id must not take the
    rest with it — this is the write path both the cyl and plate services use,
    so an over-narrow grant breaks uploads rather than the move."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO storage.buckets (id, name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (BUCKET, BUCKET),
        )
        cur.execute(f"SET LOCAL ROLE {ROLE}")
        cur.execute(
            "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s)",
            (BUCKET, "12/wave-1/P1.mp4"),
        )
        cur.execute(
            "UPDATE storage.objects SET metadata = %s, user_metadata = %s, "
            "version = %s, updated_at = now(), last_accessed_at = now() "
            "WHERE bucket_id = %s AND name = %s",
            ('{"size": 1}', "{}", "v2", BUCKET, "12/wave-1/P1.mp4"),
        )
        assert cur.rowcount == 1, "the grant is too narrow for a normal overwrite"

    pg_conn.rollback()


def test_the_role_can_overwrite_a_video_it_already_wrote(pg_conn):
    """The object key is derived from the plate, so a re-render writes the same key.
    Without the UPDATE policy that upsert fails on the second render."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO storage.buckets (id, name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (BUCKET, BUCKET),
        )
        cur.execute(f"SET LOCAL ROLE {ROLE}")
        cur.execute(
            "INSERT INTO storage.objects (bucket_id, name) VALUES (%s, %s)",
            (BUCKET, "12/wave-1/P1.mp4"),
        )
        cur.execute(
            "UPDATE storage.objects SET updated_at = now() "
            "WHERE bucket_id = %s AND name = %s",
            (BUCKET, "12/wave-1/P1.mp4"),
        )
        assert cur.rowcount == 1, "the role cannot update an object it just wrote"

    pg_conn.rollback()
