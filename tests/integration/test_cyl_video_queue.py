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
                "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s", (scan_id,)
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
                "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s", (scan_id,)
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
    try:
        with pg_conn.cursor() as cur:
            for sig in wrappers:
                for role in ("anon", "authenticated"):
                    cur.execute(
                        "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig)
                    )
                    assert cur.fetchone()[0] is False, f"{role} must NOT execute {sig}"
                cur.execute(
                    "SELECT has_function_privilege('bloom_workflows', %s, 'EXECUTE')", (sig,)
                )
                assert cur.fetchone()[0] is True, f"bloom_workflows must execute {sig}"
    finally:
        pg_conn.rollback()


def test_cyl_video_jobs_readable_by_bloom_user(pg_conn):
    # The read policy must target the roles a real session actually holds (bloom_user via the JWT
    # hook), not the raw `authenticated` role — otherwise the frontend poll can never read the
    # table. Runs as bloom_user (RLS applies, unlike the BYPASSRLS supabase_admin the suite uses).
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 42))  # seed one job
            cur.execute("SET LOCAL ROLE bloom_user")
            cur.execute(
                "SELECT count(*) FROM public.cyl_video_jobs WHERE scan_id = %s", (scan_id,)
            )
            assert cur.fetchone()[0] == 1  # a real user session can read its job status
            cur.execute("RESET ROLE")
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
            cur.execute("SELECT msg_id FROM public.cyl_video_jobs WHERE id = %s", (job_id,))
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
            assert job["status"] == "failed"   # dead-lettered by read_ct...
            assert job["attempts"] == 0        # ...even though fail_job never ran
    finally:
        pg_conn.rollback()
