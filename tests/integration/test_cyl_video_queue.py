"""Integration tests for the cyl-video queue wrappers against the live compose DB.

Exercises the SECURITY DEFINER wrappers (enqueue/claim/complete/fail) and the
cyl_video_jobs status table end-to-end over real pgmq — the layer the worker
unit tests mock out. Seeds a temporary cyl_scans row, runs the queue lifecycle,
then rolls back so nothing persists (uses `pg_conn`, connected as supabase_admin).
"""


def _new_scan(cur) -> int:
    """Create a throwaway cyl_scans row (all columns nullable) and return its id."""
    cur.execute("INSERT INTO public.cyl_scans DEFAULT VALUES RETURNING id")
    return cur.fetchone()[0]


def _job(cur, job_id):
    cur.execute(
        "SELECT status, scan_id, experiment_id, msg_id, attempts, path, error "
        "FROM public.cyl_video_jobs WHERE id = %s",
        (job_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    keys = ["status", "scan_id", "experiment_id", "msg_id", "attempts", "path", "error"]
    return dict(zip(keys, row))


def test_enqueue_creates_queued_job(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 42))
            job_id = cur.fetchone()[0]

            job = _job(cur, job_id)
            assert job["status"] == "queued"
            assert job["scan_id"] == scan_id
            assert job["experiment_id"] == 42
            assert job["msg_id"] is not None  # pgmq.send returned a message id
    finally:
        pg_conn.rollback()


def test_enqueue_skips_when_video_already_exists(pg_conn):
    # A scan that already has a video (cyl_scan_videos row) must not be re-enqueued: enqueue
    # returns NULL and creates no job, so the worker never re-renders an existing video.
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute(
                "INSERT INTO public.cyl_scan_videos (scan_id, path, frames) VALUES (%s, %s, %s)",
                (scan_id, f"cyl-videos/{scan_id}.mp4", 72),
            )
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 42))
            assert cur.fetchone()[0] is None  # skipped — no job id returned
            cur.execute(
                "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s",
                (scan_id,),
            )
            assert cur.fetchone()[0] == 0  # and no job row created
    finally:
        pg_conn.rollback()


def test_enqueue_is_idempotent_per_scan(pg_conn):
    # The partial unique index + fast-path must collapse repeat enqueues into one job.
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 1))
            first = cur.fetchone()[0]
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 1))
            second = cur.fetchone()[0]

            assert first == second
            cur.execute(
                "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s",
                (scan_id,),
            )
            assert cur.fetchone()[0] == 1
    finally:
        pg_conn.rollback()


def test_claim_marks_processing_and_returns_job(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 7))
            job_id = cur.fetchone()[0]

            cur.execute("SELECT * FROM public.claim_cyl_video_job(120)")
            claimed = cur.fetchone()
            assert claimed is not None
            c_job_id, c_scan_id, c_exp_id, c_msg_id = claimed
            assert c_job_id == job_id
            assert c_scan_id == scan_id
            assert c_exp_id == 7
            assert c_msg_id is not None

            job = _job(cur, job_id)
            assert job["status"] == "processing"
    finally:
        pg_conn.rollback()


def test_complete_marks_complete_and_drops_message(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 1))
            job_id = cur.fetchone()[0]
            cur.execute("SELECT * FROM public.claim_cyl_video_job(120)")
            _, _, _, msg_id = cur.fetchone()

            cur.execute(
                "SELECT public.complete_cyl_video_job(%s, %s, %s)",
                (job_id, msg_id, f"cyl-videos/{scan_id}.mp4"),
            )
            job = _job(cur, job_id)
            assert job["status"] == "complete"
            assert job["path"] == f"cyl-videos/{scan_id}.mp4"

            # Message is gone: a fresh claim finds nothing.
            cur.execute("SELECT * FROM public.claim_cyl_video_job(120)")
            assert cur.fetchone() is None
    finally:
        pg_conn.rollback()


def test_fail_is_terminal(pg_conn):
    # Retry/requeue is deferred (see local follow-ups): a single failure marks the job 'failed'
    # and dead-letters the message immediately — no 'queued' redelivery.
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 1))
            job_id = cur.fetchone()[0]
            cur.execute("SELECT * FROM public.claim_cyl_video_job(120)")
            _, _, _, msg_id = cur.fetchone()

            cur.execute(
                "SELECT public.fail_cyl_video_job(%s, %s, %s)", (job_id, msg_id, "boom")
            )
            job = _job(cur, job_id)
            assert job["status"] == "failed"  # terminal on the first failure
            assert job["attempts"] == 1
            assert job["error"] == "boom"

            # Archived, not requeued: a fresh claim finds nothing.
            cur.execute("SELECT * FROM public.claim_cyl_video_job(120)")
            assert cur.fetchone() is None
    finally:
        pg_conn.rollback()


def test_fail_does_not_clobber_completed_job(pg_conn):
    # vt-expiry / deploy race: worker A completes a job, then a straggler second worker reports a
    # failure for the same job. The 'processing'-guarded fail must NOT flip a settled job to
    # 'failed' (which would show failure for a video that exists, with no way to re-enqueue).
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 1))
            job_id = cur.fetchone()[0]
            cur.execute("SELECT * FROM public.claim_cyl_video_job(120)")
            _, _, _, msg_id = cur.fetchone()

            path = f"cyl-videos/{scan_id}.mp4"
            cur.execute(
                "SELECT public.complete_cyl_video_job(%s, %s, %s)",
                (job_id, msg_id, path),
            )
            cur.execute(
                "SELECT public.fail_cyl_video_job(%s, %s, %s)",
                (job_id, msg_id, "late boom"),
            )
            job = _job(cur, job_id)
            assert job["status"] == "complete"  # not clobbered to 'failed'
            assert job["path"] == path
    finally:
        pg_conn.rollback()


def test_wrappers_denied_to_public(pg_conn):
    # The SECURITY DEFINER wrappers are PostgREST-exposed (public schema). EXECUTE
    # must be revoked from PUBLIC so a direct /rest/v1/rpc call as anon/authenticated
    # can't bypass the API's auth + rate limit. Only bloom_workflows may call them.
    wrappers = [
        "public.enqueue_cyl_video(bigint, bigint)",
        "public.claim_cyl_video_job(integer, integer)",
        "public.complete_cyl_video_job(uuid, bigint, text)",
        "public.fail_cyl_video_job(uuid, bigint, text, integer)",
    ]
    # Also cover service_role and the JWT-hook session roles: none may call the wrappers directly.
    denied = (
        "anon",
        "authenticated",
        "service_role",
        "bloom_user",
        "bloom_writer",
        "bloom_admin",
    )
    try:
        with pg_conn.cursor() as cur:
            for sig in wrappers:
                for role in denied:
                    cur.execute(
                        "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig)
                    )
                    assert cur.fetchone()[0] is False, f"{role} must NOT execute {sig}"
                cur.execute(
                    "SELECT has_function_privilege('bloom_workflows', %s, 'EXECUTE')",
                    (sig,),
                )
                assert cur.fetchone()[0] is True, f"bloom_workflows must execute {sig}"
    finally:
        pg_conn.rollback()


def test_cyl_video_jobs_readable_by_session_roles(pg_conn):
    # Every role the read policy names — bloom_user/writer/admin, the roles a real session gets
    # from the JWT hook — must actually be able to read job status (needs both the policy and the
    # table GRANT). Runs as each role (RLS applies, unlike the BYPASSRLS supabase_admin the suite
    # uses) rather than trusting default privileges.
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute(
                "SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 42)
            )  # seed one job
            for role in ("bloom_user", "bloom_writer", "bloom_admin"):
                cur.execute("SAVEPOINT s")
                cur.execute(f"SET LOCAL ROLE {role}")
                cur.execute(
                    "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s",
                    (scan_id,),
                )
                assert cur.fetchone()[0] == 1, f"{role} must read its job status"
                cur.execute("ROLLBACK TO SAVEPOINT s")  # restores role
    finally:
        pg_conn.rollback()


def test_claim_dead_letters_poison_message(pg_conn):
    # A job that hard-crashes the worker never calls fail_job (attempts stays 0),
    # so dead-lettering must fall back to pgmq's read_ct. Simulate a message that
    # has been redelivered many times and confirm claim dead-letters it.
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 1))
            job_id = cur.fetchone()[0]
            cur.execute(
                "SELECT msg_id FROM public.cyl_video_jobs WHERE id = %s", (job_id,)
            )
            msg_id = cur.fetchone()[0]

            # Pretend the worker has already crashed on this message many times.
            cur.execute(
                "UPDATE pgmq.q_cyl_video_generation SET read_ct = 10 WHERE msg_id = %s",
                (msg_id,),
            )

            # max_reads below read_ct -> dead-letter, hand nothing to the worker.
            cur.execute("SELECT * FROM public.claim_cyl_video_job(120, 5)")
            assert cur.fetchone() is None

            job = _job(cur, job_id)
            assert job["status"] == "failed"  # dead-lettered by read_ct...
            assert job["attempts"] == 0  # ...even though fail_job never ran

            # The message was genuinely archived (moved to pgmq's archive table), not merely
            # hidden — a hidden message would also make the claim above return None.
            cur.execute(
                "SELECT count(*) FROM pgmq.a_cyl_video_generation WHERE msg_id = %s",
                (msg_id,),
            )
            assert cur.fetchone()[0] == 1
    finally:
        pg_conn.rollback()


def test_complete_is_idempotent_on_redelivery(pg_conn):
    # If a completion RPC is lost, the message redelivers and the worker re-completes. Calling
    # complete_cyl_video_job twice (the msg_id already deleted on the first call) must be a
    # harmless no-op that leaves the job 'complete' — the worker's "leave for idempotent
    # completion" strategy (worker.py) depends on this.
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 1))
            job_id = cur.fetchone()[0]
            cur.execute("SELECT * FROM public.claim_cyl_video_job(120)")
            _, _, _, msg_id = cur.fetchone()

            path = f"cyl-videos/{scan_id}.mp4"
            cur.execute(
                "SELECT public.complete_cyl_video_job(%s, %s, %s)",
                (job_id, msg_id, path),
            )
            # Second call with the same (already-deleted) msg_id must not raise.
            cur.execute(
                "SELECT public.complete_cyl_video_job(%s, %s, %s)",
                (job_id, msg_id, path),
            )
            job = _job(cur, job_id)
            assert job["status"] == "complete"  # still complete, no error
            assert job["path"] == path
    finally:
        pg_conn.rollback()


def test_cyl_video_jobs_hidden_from_anon_and_authenticated(pg_conn):
    # The read policy targets bloom_user/writer/admin only. Raw anon/authenticated (a session
    # that never got a JWT-hook role) must not read job status — either RLS hides every row
    # (count 0) or the role lacks SELECT entirely (permission denied). Both are acceptable;
    # what must NOT happen is a raw role reading another lab's job rows.
    import psycopg

    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 1))
            for role in ("anon", "authenticated"):
                cur.execute("SAVEPOINT s")
                cur.execute(f"SET LOCAL ROLE {role}")
                try:
                    cur.execute(
                        "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s",
                        (scan_id,),
                    )
                    seen = cur.fetchone()[0]
                except psycopg.errors.InsufficientPrivilege:
                    seen = "denied"
                cur.execute(
                    "ROLLBACK TO SAVEPOINT s"
                )  # clears any abort + restores role
                assert seen in (0, "denied"), (
                    f"{role} must not read job rows, saw {seen}"
                )
    finally:
        pg_conn.rollback()


def test_concurrent_enqueue_dedupes_to_one_job(pg_conn, pg_conninfo):
    # Two INDEPENDENT connections enqueue the SAME scan at once. The partial unique index +
    # the unique_violation handler must collapse them to ONE job (both calls returning the same
    # job_id, exactly one row). This exercises the real race the single-connection idempotency
    # test cannot — that one enqueues twice in one transaction and only hits the read fast-path.
    import threading

    import psycopg

    with psycopg.connect(pg_conninfo, autocommit=True) as seed, seed.cursor() as cur:
        cur.execute("INSERT INTO public.cyl_scans DEFAULT VALUES RETURNING id")
        scan_id = cur.fetchone()[0]

    results = {}
    barrier = threading.Barrier(2)

    def racer(key):
        with psycopg.connect(pg_conninfo, autocommit=True) as c, c.cursor() as cur:
            barrier.wait()  # fire both enqueues as simultaneously as possible
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 1))
            results[key] = cur.fetchone()[0]

    try:
        threads = [
            threading.Thread(target=racer, args=(k,), daemon=True) for k in ("a", "b")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert (
            results.get("a") is not None and results["a"] == results["b"]
        )  # one job_id
        with psycopg.connect(pg_conninfo, autocommit=True) as c, c.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s",
                (scan_id,),
            )
            assert cur.fetchone()[0] == 1  # exactly one job row, not two
    finally:
        # Rows are committed (independent connections), so clean up explicitly, including the
        # one pgmq message the winning enqueue sent (the loser's unique_violation sent none).
        with psycopg.connect(pg_conninfo, autocommit=True) as c, c.cursor() as cur:
            cur.execute(
                "SELECT msg_id FROM public.cyl_video_jobs WHERE scan_id = %s",
                (scan_id,),
            )
            for (mid,) in cur.fetchall():
                if mid is not None:
                    cur.execute(
                        "SELECT pgmq.delete('cyl_video_generation', %s)", (mid,)
                    )
            cur.execute(
                "DELETE FROM public.cyl_video_jobs WHERE scan_id = %s", (scan_id,)
            )
            cur.execute("DELETE FROM public.cyl_scans WHERE id = %s", (scan_id,))
