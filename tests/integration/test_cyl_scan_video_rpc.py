"""
Integration tests for `record_cyl_scan_video(bigint, text, integer)`.

The unit tests beside this one read the migration's *text*. They cannot tell whether the SQL
applies, whether `bloom_workflows` may execute it, or whether RLS lets the insert through —
and every one of those failures is silent in production, because `_record_video` catches its
exception and logs a warning. Generation returns 200 with a playable video and records
nothing. That is exactly how the table came to hold no rows against 84,748 stored videos, and
a pattern match over a `.sql` file cannot catch it happening again.

So these exercise the function against a real database, as the roles that call it.

LOCAL ONLY: `pg_conn` connects to 127.0.0.1 on POSTGRES_HOST_PORT as a BYPASSRLS superuser and
every test rolls back. Role behaviour is exercised with `SET LOCAL ROLE`. Runs in CI's
`compose-health-check` job.
"""

import pytest

# Skip the whole module if psycopg isn't available (matches the sibling tests).
psycopg = pytest.importorskip("psycopg")

RPC = "public.record_cyl_scan_video"


def _seed_scan(cur) -> int:
    """One scan row, to satisfy cyl_scan_videos.scan_id -> cyl_scans(id)."""
    cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
    return cur.fetchone()[0]


def _row(cur, scan_id):
    cur.execute(
        "SELECT path, frames FROM public.cyl_scan_videos WHERE scan_id = %s", (scan_id,)
    )
    return cur.fetchone()


def test_bloom_workflows_can_record_a_video(pg_conn):
    """The whole point: the role the service runs as can actually write the row.

    A missing grant, a wrong owner, or an RLS denial all fail here and nowhere else.
    """
    with pg_conn.cursor() as cur:
        scan_id = _seed_scan(cur)
        cur.execute("SET LOCAL ROLE bloom_workflows")
        cur.execute(f"SELECT {RPC}(%s, %s, %s)", (scan_id, f"cyl-videos/{scan_id}.mp4", 72))
        cur.execute("RESET ROLE")

        assert _row(cur, scan_id) == (f"cyl-videos/{scan_id}.mp4", 72)
    pg_conn.rollback()


def test_a_second_call_updates_the_same_row(pg_conn):
    """One row per scan. The upsert this replaced could not do it — it wrote the conflict key,
    which bloom_workflows cannot update, so postgres refused the statement outright."""
    with pg_conn.cursor() as cur:
        scan_id = _seed_scan(cur)
        cur.execute("SET LOCAL ROLE bloom_workflows")
        cur.execute(f"SELECT {RPC}(%s, %s, %s)", (scan_id, "cyl-videos/a.mp4", 30))
        cur.execute(f"SELECT {RPC}(%s, %s, %s)", (scan_id, "cyl-videos/b.mp4", 72))
        cur.execute("RESET ROLE")

        cur.execute(
            "SELECT count(*) FROM public.cyl_scan_videos WHERE scan_id = %s", (scan_id,)
        )
        assert cur.fetchone()[0] == 1
        assert _row(cur, scan_id) == ("cyl-videos/b.mp4", 72)
    pg_conn.rollback()


def test_a_null_count_never_overwrites_a_measured_one(pg_conn):
    """A null means "nobody measured this", which is strictly less than a real count.

    The caller checks before writing a null, but that check and the write are not one
    operation, and the queue worker runs the same wrapper from another process — so a real
    count can appear in between. COALESCE is what makes the write safe under that interleaving.
    """
    with pg_conn.cursor() as cur:
        scan_id = _seed_scan(cur)
        cur.execute("SET LOCAL ROLE bloom_workflows")
        cur.execute(f"SELECT {RPC}(%s, %s, %s)", (scan_id, "cyl-videos/a.mp4", 72))
        cur.execute(f"SELECT {RPC}(%s, %s, %s)", (scan_id, "cyl-videos/a.mp4", None))
        cur.execute("RESET ROLE")

        assert _row(cur, scan_id) == ("cyl-videos/a.mp4", 72)
    pg_conn.rollback()


def test_a_null_count_is_accepted_when_nothing_is_recorded(pg_conn):
    """A stored video nobody has measured is recorded without claiming a length for it."""
    with pg_conn.cursor() as cur:
        scan_id = _seed_scan(cur)
        cur.execute("SET LOCAL ROLE bloom_workflows")
        cur.execute(f"SELECT {RPC}(%s, %s, %s)", (scan_id, "cyl-videos/a.mp4", None))
        cur.execute("RESET ROLE")

        assert _row(cur, scan_id) == ("cyl-videos/a.mp4", None)
    pg_conn.rollback()


def test_the_scan_id_of_an_existing_row_is_never_rewritten(pg_conn):
    """`scan_id` is matched on, not set — which keeps a row from being repointed at another
    scan, and is the reason this is a function rather than a client-side upsert."""
    with pg_conn.cursor() as cur:
        first = _seed_scan(cur)
        second = _seed_scan(cur)
        cur.execute("SET LOCAL ROLE bloom_workflows")
        cur.execute(f"SELECT {RPC}(%s, %s, %s)", (first, "cyl-videos/a.mp4", 10))
        cur.execute(f"SELECT {RPC}(%s, %s, %s)", (second, "cyl-videos/b.mp4", 20))
        cur.execute("RESET ROLE")

        assert _row(cur, first) == ("cyl-videos/a.mp4", 10)
        assert _row(cur, second) == ("cyl-videos/b.mp4", 20)
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["anon", "authenticated", "service_role"])
def test_untrusted_roles_cannot_record_a_video(pg_conn, role):
    """It is SECURITY DEFINER on a table these roles cannot write, and it is reachable at
    /rest/v1/rpc — so EXECUTE has to be revoked, not merely unused."""
    with pg_conn.cursor() as cur:
        scan_id = _seed_scan(cur)
        cur.execute(f"SET LOCAL ROLE {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(f"SELECT {RPC}(%s, %s, %s)", (scan_id, "cyl-videos/x.mp4", 1))
    pg_conn.rollback()


def test_the_function_is_owned_by_a_role_that_can_write_the_table(pg_conn):
    """SECURITY DEFINER runs as the owner. cyl_scan_videos has RLS with policies for
    bloom_workflows only, so an owner without the rights to write it makes every call fail —
    and `_record_video` logs that rather than raising."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_userbyid(p.proowner), p.prosecdef
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'public' AND p.proname = 'record_cyl_scan_video'
            """
        )
        owner, security_definer = cur.fetchone()

        assert security_definer is True
        cur.execute("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = %s", (owner,))
        bypassrls, superuser = cur.fetchone()
        assert bypassrls or superuser, (
            f"record_cyl_scan_video is owned by {owner}, which cannot write past "
            f"cyl_scan_videos' RLS policies"
        )
    pg_conn.rollback()
