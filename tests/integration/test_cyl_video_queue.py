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


def test_fail_retries_then_dead_letters(pg_conn):
    try:
        with pg_conn.cursor() as cur:
            scan_id = _new_scan(cur)
            cur.execute("SELECT public.enqueue_cyl_video(%s, %s)", (scan_id, 1))
            job_id = cur.fetchone()[0]
            cur.execute("SELECT * FROM public.claim_cyl_video_job(120)")
            _, _, _, msg_id = cur.fetchone()

            # Under max attempts: back to 'queued' for redelivery.
            cur.execute(
                "SELECT public.fail_cyl_video_job(%s, %s, %s, %s)",
                (job_id, msg_id, "boom", 3),
            )
            job = _job(cur, job_id)
            assert job["status"] == "queued"
            assert job["attempts"] == 1
            assert job["error"] == "boom"

            # Reach max attempts: dead-lettered and marked failed.
            for _ in range(2):
                cur.execute(
                    "SELECT public.fail_cyl_video_job(%s, %s, %s, %s)",
                    (job_id, msg_id, "boom", 3),
                )
            job = _job(cur, job_id)
            assert job["status"] == "failed"
            assert job["attempts"] == 3
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
