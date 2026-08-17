"""Integration tests for the cyl-video queue wrappers against the live compose DB.

Exercises the SECURITY DEFINER wrappers (enqueue/claim/complete/fail/stats), their
definer identity, and the cyl_video_jobs RLS surface end-to-end over real pgmq.
Seeds throwaway cyl_experiments/cyl_scans rows and rolls back, so nothing persists
(uses `pg_conn`, connected as supabase_admin) — except the concurrency test, which
must commit and cleans up after itself.
"""

import sys
import threading
import warnings

import psycopg
import pytest

WRAPPERS = [
    "public.enqueue_cyl_video(bigint, bigint)",
    "public.claim_cyl_video_job(integer, integer)",
    "public.complete_cyl_video_job(uuid, bigint, text)",
    "public.fail_cyl_video_job(uuid, bigint, text)",
    "public.cyl_video_queue_stats()",
]

DEFINER_ROLE = "bloom_video_queue_owner"
SESSION_ROLES = ["bloom_user", "bloom_writer", "bloom_admin"]

JOB_COLUMNS = ["status", "scan_id", "experiment_id", "msg_id", "path", "error"]


def _new_experiment(cur) -> int:
    cur.execute(
        "INSERT INTO public.cyl_experiments (name) VALUES ('queue-test') RETURNING id"
    )
    return cur.fetchone()[0]


def _new_scan(cur) -> int:
    """Create a throwaway cyl_scans row (all columns nullable) and return its id."""
    cur.execute("INSERT INTO public.cyl_scans DEFAULT VALUES RETURNING id")
    return cur.fetchone()[0]


def _seed(cur) -> tuple[int, int]:
    return _new_scan(cur), _new_experiment(cur)


def _job(cur, job_id):
    cur.execute(
        f"SELECT {', '.join(JOB_COLUMNS)} FROM public.cyl_video_jobs WHERE id = %s",
        (job_id,),
    )
    row = cur.fetchone()
    return dict(zip(JOB_COLUMNS, row)) if row else None


def _enqueue(cur, scan_id, experiment_id):
    cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, experiment_id))
    return cur.fetchone()[0]


def _claim(cur, vt=120, max_reads=5):
    cur.execute("SELECT * FROM public.claim_cyl_video_job(%s, %s)", (vt, max_reads))
    return cur.fetchone()


# --- lifecycle -------------------------------------------------------------


def test_enqueue_creates_queued_job(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            job_id = _enqueue(cur, scan_id, experiment_id)

            job = _job(cur, job_id)
            assert job["status"] == "queued"
            assert job["scan_id"] == scan_id
            assert job["experiment_id"] == experiment_id
            assert job["msg_id"] is not None  # pgmq.send returned a message id
    finally:
        pg_conn.rollback()


def test_enqueue_skips_when_video_already_exists(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            cur.execute(
                "INSERT INTO public.cyl_scan_videos (scan_id, path, frames) "
                "VALUES (%s, %s, %s)",
                (scan_id, f"cyl-videos/{scan_id}.mp4", 72),
            )
            assert _enqueue(cur, scan_id, experiment_id) is None

            cur.execute(
                "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s",
                (scan_id,),
            )
            assert cur.fetchone()[0] == 0
    finally:
        pg_conn.rollback()


def test_enqueue_rejects_an_unknown_experiment(pg_conn):
    # experiment_id carries a FK, so a job can never be attributed to an experiment
    # that does not exist.
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                _enqueue(cur, scan_id, 2_147_483_647)
    finally:
        pg_conn.rollback()


def test_claim_marks_processing_and_returns_the_payload(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            job_id = _enqueue(cur, scan_id, experiment_id)

            claimed = _claim(cur)
            assert claimed is not None
            assert claimed[0] == job_id
            assert claimed[1] == scan_id
            assert claimed[2] == experiment_id
            assert claimed[3] is not None  # msg_id
            assert _job(cur, job_id)["status"] == "processing"
    finally:
        pg_conn.rollback()


def test_complete_records_the_path_and_drops_the_message(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            job_id = _enqueue(cur, scan_id, experiment_id)
            _, _, _, msg_id = _claim(cur)

            path = f"cyl-videos/{scan_id}.mp4"
            cur.execute(
                "SELECT public.complete_cyl_video_job(%s, %s, %s)",
                (job_id, msg_id, path),
            )
            job = _job(cur, job_id)
            assert job["status"] == "complete"
            assert job["path"] == path

            cur.execute("SELECT count(*) FROM pgmq.q_cyl_video_generation WHERE msg_id = %s", (msg_id,))
            assert cur.fetchone()[0] == 0
    finally:
        pg_conn.rollback()


def test_fail_marks_failed_and_archives_the_message(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            job_id = _enqueue(cur, scan_id, experiment_id)
            _, _, _, msg_id = _claim(cur)

            cur.execute(
                "SELECT public.fail_cyl_video_job(%s, %s, %s)",
                (job_id, msg_id, "video generation failed (internal error)"),
            )
            job = _job(cur, job_id)
            assert job["status"] == "failed"
            assert job["error"] == "video generation failed (internal error)"

            cur.execute("SELECT count(*) FROM pgmq.a_cyl_video_generation WHERE msg_id = %s", (msg_id,))
            assert cur.fetchone()[0] == 1
    finally:
        pg_conn.rollback()


def test_complete_does_not_clobber_a_settled_job(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            job_id = _enqueue(cur, scan_id, experiment_id)
            _, _, _, msg_id = _claim(cur)
            cur.execute(
                "SELECT public.fail_cyl_video_job(%s, %s, %s)", (job_id, msg_id, "boom")
            )

            # A late second worker completing the same job must not resurrect it.
            cur.execute(
                "SELECT public.complete_cyl_video_job(%s, %s, %s)",
                (job_id, msg_id, "cyl-videos/late.mp4"),
            )
            job = _job(cur, job_id)
            assert job["status"] == "failed"
            assert job["path"] is None
    finally:
        pg_conn.rollback()


def test_enqueue_reuses_an_in_flight_job(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            first = _enqueue(cur, scan_id, experiment_id)
            assert _enqueue(cur, scan_id, experiment_id) == first

            cur.execute(
                "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s",
                (scan_id,),
            )
            assert cur.fetchone()[0] == 1
    finally:
        pg_conn.rollback()


# --- redelivery and poison messages ----------------------------------------


def test_an_expired_lease_redelivers_the_same_job(pg_conn):
    # vt=0 makes the message immediately visible again. Re-claiming must hand back the
    # same job — this is how a job whose worker died mid-render is recovered.
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            job_id = _enqueue(cur, scan_id, experiment_id)

            first = _claim(cur, vt=0)
            second = _claim(cur, vt=0)
            assert second is not None, "an expired lease must be re-claimable"
            assert second[0] == first[0] == job_id
            assert second[3] == first[3]  # same msg_id

            cur.execute(
                "SELECT read_ct FROM pgmq.q_cyl_video_generation WHERE msg_id = %s",
                (first[3],),
            )
            assert cur.fetchone()[0] == 2
    finally:
        pg_conn.rollback()


def test_claim_dead_letters_a_poison_message(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            job_id = _enqueue(cur, scan_id, experiment_id)
            cur.execute("SELECT msg_id FROM public.cyl_video_jobs WHERE id = %s", (job_id,))
            msg_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE pgmq.q_cyl_video_generation SET read_ct = 10 WHERE msg_id = %s",
                (msg_id,),
            )

            assert _claim(cur, max_reads=5) is None  # not handed to the worker
            job = _job(cur, job_id)
            assert job["status"] == "failed"
            assert "dead-lettered" in job["error"]
    finally:
        pg_conn.rollback()


def test_dead_lettering_leaves_a_settled_job_alone(pg_conn):
    # The dead-letter write is the one state transition that could otherwise overwrite
    # a job that already succeeded.
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            job_id = _enqueue(cur, scan_id, experiment_id)
            _, _, _, msg_id = _claim(cur)
            path = f"cyl-videos/{scan_id}.mp4"
            cur.execute(
                "SELECT public.complete_cyl_video_job(%s, %s, %s)", (job_id, msg_id, path)
            )

            # Re-send a stray message for the now-complete job, past the read limit.
            cur.execute(
                "SELECT pgmq.send('cyl_video_generation', "
                "jsonb_build_object('job_id', %s::text, 'scan_id', %s, 'experiment_id', %s))",
                (job_id, scan_id, experiment_id),
            )
            stray = cur.fetchone()[0]
            cur.execute(
                "UPDATE pgmq.q_cyl_video_generation SET read_ct = 10 WHERE msg_id = %s",
                (stray,),
            )
            assert _claim(cur, max_reads=5) is None

            job = _job(cur, job_id)
            assert job["status"] == "complete", "a settled job must keep its outcome"
            assert job["path"] == path
    finally:
        pg_conn.rollback()


def test_an_unparseable_message_is_archived_not_left_at_the_head(pg_conn):
    # A cast failure must not abort the statement: that would roll back pgmq's read_ct
    # increment and wedge the queue behind a message the poison guard can never reach.
    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT pgmq.send('cyl_video_generation', '{\"job_id\": \"not-a-uuid\"}'::jsonb)"
            )
            msg_id = cur.fetchone()[0]

            assert _claim(cur) is None
            cur.execute(
                "SELECT count(*) FROM pgmq.q_cyl_video_generation WHERE msg_id = %s", (msg_id,)
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT count(*) FROM pgmq.a_cyl_video_generation WHERE msg_id = %s", (msg_id,)
            )
            assert cur.fetchone()[0] == 1
    finally:
        pg_conn.rollback()


# --- stats -----------------------------------------------------------------


def test_queue_stats_counts_each_state(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            # Both deletes are rolled back below. Clearing the queue is what makes
            # the two blind claims deterministic: a message left committed by the
            # concurrency test is otherwise what the first claim consumes, leaving
            # the fail path a no-op and failing this test for an unrelated reason.
            cur.execute("DELETE FROM public.cyl_video_jobs")
            cur.execute("DELETE FROM pgmq.q_cyl_video_generation")
            scan_id, experiment_id = _seed(cur)
            _enqueue(cur, scan_id, experiment_id)

            failed_scan = _new_scan(cur)
            failed_job = _enqueue(cur, failed_scan, experiment_id)
            _claim(cur)
            _claim(cur)
            cur.execute(
                "SELECT msg_id FROM public.cyl_video_jobs WHERE id = %s", (failed_job,)
            )
            cur.execute(
                "SELECT public.fail_cyl_video_job(%s, %s, %s)",
                (failed_job, cur.fetchone()[0], "boom"),
            )

            cur.execute("SELECT queued, processing, failed FROM public.cyl_video_queue_stats()")
            queued, processing, failed = cur.fetchone()
            assert failed == 1, "a silently-discarding queue must be distinguishable from an idle one"
            assert queued + processing + failed == 2
    finally:
        pg_conn.rollback()


# --- definer identity ------------------------------------------------------


def test_wrappers_are_owned_by_the_pinned_definer_role(pg_conn):
    # Left unpinned these inherit whoever applied the migration — in practice a
    # superuser — so five small functions would run with unrestricted DB access.
    with pg_conn.cursor() as cur:
        for wrapper in WRAPPERS:
            cur.execute(
                "SELECT pg_get_userbyid(proowner), prosecdef, proconfig "
                "FROM pg_proc WHERE oid = %s::regprocedure",
                (wrapper,),
            )
            owner, secdef, config = cur.fetchone()
            assert owner == DEFINER_ROLE, f"{wrapper} definer identity drifted to {owner}"
            assert secdef is True, f"{wrapper} is not SECURITY DEFINER"
            assert any("search_path=" in c for c in config or []), f"{wrapper} has no pinned search_path"


def test_the_definer_role_is_not_a_superuser_and_cannot_log_in(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s",
            (DEFINER_ROLE,),
        )
        row = cur.fetchone()
        assert row is not None, f"{DEFINER_ROLE} does not exist"
        assert row == (False, False, False)


def test_the_definer_role_can_write_its_own_table(pg_conn):
    # The wrappers run as this role. It is neither the table owner nor BYPASSRLS, so a
    # GRANT alone is not enough — without a policy every wrapper is silently a no-op.
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            cur.execute("SAVEPOINT definer")
            cur.execute(f"SET LOCAL ROLE {DEFINER_ROLE}")
            cur.execute(
                "INSERT INTO public.cyl_video_jobs (scan_id, experiment_id) "
                "VALUES (%s, %s) RETURNING id",
                (scan_id, experiment_id),
            )
            job_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE public.cyl_video_jobs SET status = 'processing' WHERE id = %s",
                (job_id,),
            )
            assert cur.rowcount == 1, "definer role cannot update its own table"
            cur.execute(
                "SELECT count(*) FROM public.cyl_video_jobs WHERE id = %s", (job_id,)
            )
            assert cur.fetchone()[0] == 1, "definer role cannot read its own table"
            cur.execute("ROLLBACK TO SAVEPOINT definer")
    finally:
        pg_conn.rollback()


def test_the_definer_role_can_read_cyl_scan_videos(pg_conn):
    # enqueue's "skip a scan that already has a video" check reads this table. Without a
    # policy the check fails open and every scan is re-rendered.
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute(
                "INSERT INTO public.cyl_scan_videos (scan_id, path, frames) "
                "VALUES (%s, %s, %s)",
                (scan_id, f"cyl-videos/{scan_id}.mp4", 72),
            )
            cur.execute("SAVEPOINT definer")
            cur.execute(f"SET LOCAL ROLE {DEFINER_ROLE}")
            cur.execute(
                "SELECT count(*) FROM public.cyl_scan_videos WHERE scan_id = %s", (scan_id,)
            )
            assert cur.fetchone()[0] == 1, "definer role cannot see existing videos"
            cur.execute("ROLLBACK TO SAVEPOINT definer")
    finally:
        pg_conn.rollback()


@pytest.mark.parametrize("table", ["cyl_video_jobs", "cyl_scan_videos"])
def test_force_row_level_security_stays_off(pg_conn, table):
    # FORCE RLS would apply policies to the table owner too, which is the one thing that
    # could re-break the definer path after it is fixed.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (f"public.{table}",),
        )
        assert cur.fetchone()[0] is False


def test_the_definer_role_is_not_reachable_from_a_jwt(pg_conn):
    # authenticator is the role PostgREST switches from, so anything granted to it is
    # assumable via a JWT role claim. The definer identity must not be.
    with pg_conn.cursor() as cur:
        cur.execute("SELECT pg_has_role('authenticator', %s, 'MEMBER')", (DEFINER_ROLE,))
        assert cur.fetchone()[0] is False


# --- authorisation ---------------------------------------------------------


@pytest.mark.parametrize("role", ["anon", "authenticated", "service_role"] + SESSION_ROLES)
def test_wrappers_are_denied_to_every_non_workflows_role(pg_conn, role):
    # Assert by calling, not by reading the catalog — catalog privileges and actual
    # call behaviour diverge (overloads, role chains).
    try:
        with pg_conn.cursor() as cur:
            cur.execute("SAVEPOINT authz")
            cur.execute(f"SET LOCAL ROLE {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT public.enqueue_cyl_video(1, 1)")
            cur.execute("ROLLBACK TO SAVEPOINT authz")
    finally:
        pg_conn.rollback()


def test_wrappers_are_executable_by_bloom_workflows(pg_conn):
    for wrapper in WRAPPERS:
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT has_function_privilege('bloom_workflows', %s, 'EXECUTE')", (wrapper,)
            )
            assert cur.fetchone()[0] is True, f"bloom_workflows cannot execute {wrapper}"


def test_bloom_roles_are_not_bypassrls(pg_conn):
    # pg_conn is supabase_admin (BYPASSRLS), so the SET LOCAL ROLE tests below only
    # mean something if the roles they switch to are not themselves exempt.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s) AND rolbypassrls",
            (SESSION_ROLES + ["bloom_workflows"],),
        )
        assert cur.fetchall() == []


@pytest.mark.parametrize("role", SESSION_ROLES)
def test_job_status_is_readable_by_session_roles(pg_conn, role):
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            _enqueue(cur, scan_id, experiment_id)

            cur.execute("SAVEPOINT rls")
            cur.execute(f"SET LOCAL ROLE {role}")
            cur.execute(
                "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s", (scan_id,)
            )
            assert cur.fetchone()[0] == 1
            cur.execute("ROLLBACK TO SAVEPOINT rls")
    finally:
        pg_conn.rollback()


@pytest.mark.parametrize("role", SESSION_ROLES + ["anon", "authenticated", "service_role"])
def test_roles_hold_no_write_privilege_on_job_status(pg_conn, role):
    # Asserted from the catalog, not by calling: an INSERT blocked by RLS raises the same
    # SQLSTATE as one blocked by privilege, so a behavioural test alone would pass even
    # with the REVOKE removed. bloom_writer is a member of authenticated, so the revoke
    # has to reach both for this to hold.
    with pg_conn.cursor() as cur:
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            cur.execute(
                "SELECT has_table_privilege(%s, 'public.cyl_video_jobs', %s)",
                (role, privilege),
            )
            assert cur.fetchone()[0] is False, f"{role} still holds {privilege}"


@pytest.mark.parametrize("role", SESSION_ROLES)
def test_session_roles_cannot_write_job_status(pg_conn, role):
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            cur.execute("SAVEPOINT w")
            cur.execute(f"SET LOCAL ROLE {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(
                    "INSERT INTO public.cyl_video_jobs (scan_id, experiment_id) VALUES (%s, %s)",
                    (scan_id, experiment_id),
                )
            cur.execute("ROLLBACK TO SAVEPOINT w")
    finally:
        pg_conn.rollback()


@pytest.mark.parametrize("role", ["anon", "authenticated", "service_role"])
def test_job_status_is_hidden_from_anon_and_authenticated(pg_conn, role):
    # service_role is BYPASSRLS, so only the REVOKE stops it — a policy would not.
    try:
        with pg_conn.cursor() as cur:
            scan_id, experiment_id = _seed(cur)
            _enqueue(cur, scan_id, experiment_id)

            cur.execute("SAVEPOINT rls")
            cur.execute(f"SET LOCAL ROLE {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT count(*) FROM public.cyl_video_jobs")
            cur.execute("ROLLBACK TO SAVEPOINT rls")
    finally:
        pg_conn.rollback()


def test_bloom_workflows_cannot_read_job_status_directly(pg_conn):
    # The sole justification for the cyl_video_queue_stats wrapper.
    try:
        with pg_conn.cursor() as cur:
            cur.execute("SAVEPOINT w")
            cur.execute("SET LOCAL ROLE bloom_workflows")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("SELECT count(*) FROM public.cyl_video_jobs")
            cur.execute("ROLLBACK TO SAVEPOINT w")
    finally:
        pg_conn.rollback()


# --- concurrency -----------------------------------------------------------


def _cleanup_concurrent_enqueue(conn, scan_id, experiment_id):
    """Undo the one test that commits, leaving no committed state behind.

    Rolls back first: a failed assertion can leave the transaction aborted, in
    which case every DELETE below would raise before deleting anything.
    """
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '10s'")
        # A committed a pgmq message as well as a row. Left behind, the next run's
        # first claim would consume it and break every downstream test.
        cur.execute(
            "DELETE FROM pgmq.q_cyl_video_generation "
            "WHERE (message->>'scan_id')::bigint = %s",
            (scan_id,),
        )
        cur.execute("DELETE FROM public.cyl_video_jobs WHERE scan_id = %s", (scan_id,))
        cur.execute("DELETE FROM public.cyl_scans WHERE id = %s", (scan_id,))
        cur.execute("DELETE FROM public.cyl_experiments WHERE id = %s", (experiment_id,))
    conn.commit()


def test_concurrent_enqueue_dedupes_to_one_job(pg_conn, pg_conninfo):
    """Force the unique_violation branch rather than hoping to race into it.

    Connection A inserts but holds its transaction open, so B's fast-path SELECT
    cannot see the row and its INSERT blocks on the partial unique index. Committing
    A then makes B's insert raise unique_violation, which is the branch under test.
    """
    with pg_conn.cursor() as cur:
        scan_id, experiment_id = _seed(cur)
    pg_conn.commit()

    result = {}

    def enqueue_on_b():
        try:
            with psycopg.connect(pg_conninfo) as conn_b, conn_b.cursor() as cur_b:
                result["b"] = _enqueue(cur_b, scan_id, experiment_id)
                conn_b.commit()
        except Exception as exc:  # surfaced below — otherwise B dies silently
            result["exc"] = exc

    conn_a = None
    # Daemon: if B never unblocks, the interpreter must still be able to exit.
    # A non-daemon thread would hang CPython at shutdown and kill the CI job on
    # the workflow timeout with no failing test named.
    thread = threading.Thread(target=enqueue_on_b, daemon=True)
    try:
        # Inside the try, so a connect failure still reaches the cleanup below —
        # the seeded scan and experiment are already committed by this point.
        conn_a = psycopg.connect(pg_conninfo)
        with conn_a.cursor() as cur_a:
            result["a"] = _enqueue(cur_a, scan_id, experiment_id)

            thread.start()
            thread.join(timeout=5)
            if "exc" in result:
                raise AssertionError(f"B failed instead of blocking: {result['exc']}")
            assert thread.is_alive(), "B should be blocked on the unique index"

            conn_a.commit()
            thread.join(timeout=10)
            assert not thread.is_alive(), "B never unblocked after A committed"

        if "exc" in result:
            raise AssertionError(f"B failed after A committed: {result['exc']}")
        assert result["b"] == result["a"], "the loser must reuse the winner's job"

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s", (scan_id,)
            )
            assert cur.fetchone()[0] == 1
    finally:
        # Whether an assertion is already propagating decides how a cleanup
        # failure is reported: raising here would replace the real diagnosis.
        failing = sys.exc_info()[0] is not None
        if thread.is_alive():  # never leave B holding a lock the cleanup needs
            thread.join(timeout=10)
        if conn_a is not None:
            conn_a.close()
        try:
            _cleanup_concurrent_enqueue(pg_conn, scan_id, experiment_id)
        except Exception as cleanup_exc:
            if not failing:
                raise
            warnings.warn(
                f"cleanup left rows behind after a failing assertion: {cleanup_exc}",
                stacklevel=2,
            )
