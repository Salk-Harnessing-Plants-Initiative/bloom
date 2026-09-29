"""The two video-generation queues: gravi_plate_video and cyl_video_generation.

Every test runs against both queues, so the cylinder half is read against the plate half
rather than reviewed on its own. Security is asserted with `has_*_privilege`, never by
calling as a denied role: invoking a SECURITY DEFINER function as a role that lacks
EXECUTE has crashed Postgres into recovery before.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

pytest.importorskip("psycopg")


@dataclass(frozen=True)
class Queue:
    name: str
    table: str
    enqueue: str
    claim: str
    progress: str
    complete: str
    fail: str
    enqueue_args: str
    claim_cols: tuple[str, ...]


PLATE = Queue(
    name="gravi_plate_video",
    table="gravi_plate_video_jobs",
    enqueue="enqueue_gravi_plate_video",
    claim="claim_gravi_plate_video_job",
    progress="report_gravi_plate_video_progress",
    complete="complete_gravi_plate_video_job",
    fail="fail_gravi_plate_video_job",
    enqueue_args="%s, %s, %s, NULL",
    claim_cols=("job_id", "experiment_id", "plate_id", "wave_number", "msg_id", "reads"),
)

CYL = Queue(
    name="cyl_video_generation",
    table="cyl_video_jobs",
    enqueue="enqueue_cyl_video",
    claim="claim_cyl_video_job",
    progress="report_cyl_video_progress",
    complete="complete_cyl_video_job",
    fail="fail_cyl_video_job",
    enqueue_args="%s, %s, NULL",
    claim_cols=("job_id", "scan_id", "experiment_id", "msg_id", "reads"),
)

QUEUES = [PLATE, CYL]
IDS = [q.name for q in QUEUES]

WRAPPERS = [
    "enqueue_gravi_plate_video(bigint,text,integer,uuid)",
    "claim_gravi_plate_video_job(integer,integer)",
    "report_gravi_plate_video_progress(uuid,bigint,text,integer,integer,integer)",
    "complete_gravi_plate_video_job(uuid,bigint,text)",
    "fail_gravi_plate_video_job(uuid,bigint,text,text)",
    "enqueue_cyl_video(bigint,bigint,uuid)",
    "claim_cyl_video_job(integer,integer)",
    "report_cyl_video_progress(uuid,bigint,text,integer,integer,integer)",
    "complete_cyl_video_job(uuid,bigint,text)",
    "fail_cyl_video_job(uuid,bigint,text,text)",
]

DENIED_ROLES = ["anon", "authenticated", "service_role"]


# --------------------------------------------------------------------------- #
# Fixture rows
# --------------------------------------------------------------------------- #
def _target(cur, queue: Queue) -> tuple:
    """Create the row(s) a job points at, and return that queue's enqueue arguments."""
    if queue is PLATE:
        cur.execute(
            "INSERT INTO gravi_experiments (name) VALUES ('queue-test') RETURNING id"
        )
        return (cur.fetchone()[0], "P1", 3)

    cur.execute("INSERT INTO cyl_experiments (name) VALUES ('queue-test') RETURNING id")
    exp_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, 1) RETURNING id",
        (exp_id,),
    )
    wave_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_plants (wave_id, qr_code) VALUES (%s, 'queue-test') RETURNING id",
        (wave_id,),
    )
    plant_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days) "
        "VALUES (%s, now(), 1) RETURNING id",
        (plant_id,),
    )
    return (cur.fetchone()[0], exp_id)


def _enqueue(cur, queue: Queue, args: tuple) -> tuple[str, bool]:
    cur.execute(
        f"SELECT job_id, created FROM public.{queue.enqueue}({queue.enqueue_args})", args
    )
    return cur.fetchone()


def _claim(cur, queue: Queue, vt: int = 300, max_reads: int = 3):
    cols = ", ".join(queue.claim_cols)
    cur.execute(f"SELECT {cols} FROM public.{queue.claim}(%s, %s)", (vt, max_reads))
    return cur.fetchone()


def _status(cur, queue: Queue, job_id) -> str | None:
    cur.execute(f"SELECT status FROM public.{queue.table} WHERE id = %s", (job_id,))
    row = cur.fetchone()
    return row[0] if row else None


def _queue_depth(cur, queue: Queue) -> int:
    cur.execute(f"SELECT count(*) FROM pgmq.q_{queue.name}")
    return cur.fetchone()[0]


def _archive_depth(cur, queue: Queue) -> int:
    cur.execute(f"SELECT count(*) FROM pgmq.a_{queue.name}")
    return cur.fetchone()[0]


@pytest.fixture
def cur(pg_conn):
    """A cursor whose work is always rolled back, so the queues stay empty between tests."""
    with pg_conn.cursor() as c:
        yield c
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Definer identity (2.3.6)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("signature", WRAPPERS)
def test_wrapper_is_definer_owned_by_postgres_with_a_pinned_search_path(cur, signature):
    """An unpinned owner resolves to a superuser on one path and to a role without pgmq
    privileges on another, so it is asserted rather than assumed."""
    cur.execute(
        "SELECT pg_get_userbyid(proowner), prosecdef, proconfig "
        "FROM pg_proc WHERE oid = %s::regprocedure",
        (f"public.{signature}",),
    )
    owner, secdef, config = cur.fetchone()
    assert owner == "postgres", f"{signature} is owned by {owner}"
    assert secdef is True, f"{signature} is not SECURITY DEFINER"
    assert config == ["search_path=pg_catalog, public, pgmq, pg_temp"], f"{signature}: {config}"


# --------------------------------------------------------------------------- #
# Privilege (2.3.5) — asserted, never invoked
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("signature", WRAPPERS)
@pytest.mark.parametrize("role", DENIED_ROLES + ["bloom_user"])
def test_wrapper_execute_is_denied_to_client_roles(cur, signature, role):
    """These live in the PostgREST-exposed public schema; EXECUTE for a client role would
    make them callable over /rest/v1/rpc."""
    cur.execute(
        "SELECT has_function_privilege(%s, %s::regprocedure, 'EXECUTE')", (role, f"public.{signature}")
    )
    assert cur.fetchone()[0] is False, f"{role} can execute {signature}"


@pytest.mark.parametrize("signature", WRAPPERS)
def test_wrapper_execute_is_granted_to_bloom_workflows(cur, signature):
    cur.execute(
        "SELECT has_function_privilege('bloom_workflows', %s::regprocedure, 'EXECUTE')",
        (f"public.{signature}",),
    )
    assert cur.fetchone()[0] is True, f"bloom_workflows cannot execute {signature}"


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
@pytest.mark.parametrize("privilege", ["INSERT", "UPDATE", "DELETE"])
@pytest.mark.parametrize("role", ["bloom_user", "bloom_writer", "bloom_admin"])
def test_job_tables_are_read_only_for_signed_in_roles(cur, queue, privilege, role):
    cur.execute(
        "SELECT has_table_privilege(%s, %s, %s)", (role, f"public.{queue.table}", privilege)
    )
    assert cur.fetchone()[0] is False, f"{role} has {privilege} on {queue.table}"


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
@pytest.mark.parametrize("role", ["bloom_user", "bloom_writer", "bloom_admin"])
@pytest.mark.parametrize("column", ["status", "stage", "frames_done", "error_code", "created_at"])
def test_signed_in_roles_may_read_the_progress_columns(cur, queue, role, column):
    cur.execute(
        "SELECT has_column_privilege(%s, %s, %s, 'SELECT')",
        (role, f"public.{queue.table}", column),
    )
    assert cur.fetchone()[0] is True, f"{role} cannot read {queue.table}.{column}"


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
@pytest.mark.parametrize("role", ["bloom_user", "bloom_writer", "bloom_admin"])
@pytest.mark.parametrize("column", ["error", "msg_id", "reads"])
def test_internal_columns_are_withheld_from_signed_in_roles(cur, queue, role, column):
    """`error` is the renderer's own failure text and carries the internal gateway host,
    the database role and PostgREST's codes; msg_id and reads are queue plumbing."""
    cur.execute(
        "SELECT has_column_privilege(%s, %s, %s, 'SELECT')",
        (role, f"public.{queue.table}", column),
    )
    assert cur.fetchone()[0] is False, f"{role} can read {queue.table}.{column}"


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
@pytest.mark.parametrize("role", DENIED_ROLES + ["bloom_agent"])
def test_denied_roles_cannot_read_any_column(cur, queue, role):
    for column in ("status", "error_code", "error", "id"):
        cur.execute(
            "SELECT has_column_privilege(%s, %s, %s, 'SELECT')",
            (role, f"public.{queue.table}", column),
        )
        assert cur.fetchone()[0] is False, f"{role} can read {queue.table}.{column}"


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
@pytest.mark.parametrize("role", DENIED_ROLES + ["bloom_agent"])
def test_job_tables_are_closed_to_unprivileged_roles(cur, queue, role):
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        cur.execute(
            "SELECT has_table_privilege(%s, %s, %s)", (role, f"public.{queue.table}", privilege)
        )
        assert cur.fetchone()[0] is False, f"{role} has {privilege} on {queue.table}"


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_row_level_security_is_enabled(cur, queue):
    cur.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (f"public.{queue.table}",))
    assert cur.fetchone()[0] is True


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_postgres_can_reach_the_pgmq_tables_the_wrappers_use(cur, queue):
    """The wrappers run as postgres and pgmq's own functions are SECURITY INVOKER, so
    without these grants every send, read, set_vt, delete and archive fails."""
    for table, privileges in (
        (f"pgmq.q_{queue.name}", ("SELECT", "INSERT", "UPDATE", "DELETE")),
        (f"pgmq.a_{queue.name}", ("SELECT", "INSERT")),
    ):
        for privilege in privileges:
            cur.execute("SELECT has_table_privilege('postgres', %s, %s)", (table, privilege))
            assert cur.fetchone()[0] is True, f"postgres lacks {privilege} on {table}"


# --------------------------------------------------------------------------- #
# Enqueue (2.3.1)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_enqueue_creates_a_queued_job_and_a_message(cur, queue):
    args = _target(cur, queue)
    before = _queue_depth(cur, queue)
    job_id, created = _enqueue(cur, queue, args)
    assert created is True
    assert _status(cur, queue, job_id) == "queued"
    assert _queue_depth(cur, queue) == before + 1


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_a_second_enqueue_shares_the_active_job(cur, queue):
    """A double click, or two people on the same item, share one render."""
    args = _target(cur, queue)
    first, created_first = _enqueue(cur, queue, args)
    depth = _queue_depth(cur, queue)
    second, created_second = _enqueue(cur, queue, args)
    assert created_second is False
    assert second == first
    assert _queue_depth(cur, queue) == depth, "a shared job must not send a second message"
    assert created_first is True


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_enqueue_after_completion_creates_a_new_job(cur, queue):
    args = _target(cur, queue)
    first, _ = _enqueue(cur, queue, args)
    claimed = _claim(cur, queue)
    msg_id = claimed[queue.claim_cols.index("msg_id")]
    cur.execute(f"SELECT public.{queue.complete}(%s, %s, 'rendered')", (first, msg_id))
    assert cur.fetchone()[0] is True

    second, created = _enqueue(cur, queue, args)
    assert created is True
    assert second != first


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_one_active_job_per_item_is_enforced_by_an_index(cur, queue):
    """The index, not the enqueue's read-then-insert, is what holds under concurrency."""
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    columns = (
        "(experiment_id, plate_id, wave_number, status)" if queue is PLATE
        else "(scan_id, experiment_id, status)"
    )
    values = "(%s, %s, %s, 'queued')" if queue is PLATE else "(%s, %s, 'queued')"
    import psycopg

    cur.execute("SAVEPOINT before_duplicate")
    with pytest.raises(psycopg.errors.UniqueViolation):
        cur.execute(f"INSERT INTO public.{queue.table} {columns} VALUES {values}", args)
    cur.execute("ROLLBACK TO SAVEPOINT before_duplicate")
    assert _status(cur, queue, job_id) == "queued"


# --------------------------------------------------------------------------- #
# Claim (2.3.2)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_claim_marks_the_job_rendering_and_returns_it(cur, queue):
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    claimed = _claim(cur, queue)
    assert claimed is not None
    assert claimed[queue.claim_cols.index("job_id")] == job_id
    assert _status(cur, queue, job_id) == "rendering"


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_claim_returns_nothing_when_the_queue_is_empty(cur, queue):
    assert _claim(cur, queue) is None


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_a_claimed_message_is_invisible_to_the_next_claim(cur, queue):
    """Two workers cannot hold the same job: the first claim hides the message for p_vt."""
    args = _target(cur, queue)
    _enqueue(cur, queue, args)
    assert _claim(cur, queue, vt=300) is not None
    assert _claim(cur, queue, vt=300) is None


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_claim_records_the_delivery_count_and_keeps_the_first_start_time(cur, queue):
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    _claim(cur, queue, vt=0)
    cur.execute(f"SELECT reads, started_at FROM public.{queue.table} WHERE id = %s", (job_id,))
    reads, started_at = cur.fetchone()
    assert reads == 1
    _claim(cur, queue, vt=0)
    cur.execute(f"SELECT reads, started_at FROM public.{queue.table} WHERE id = %s", (job_id,))
    reads_again, started_again = cur.fetchone()
    assert reads_again == 2
    assert started_again == started_at, "started_at means when work first began"


# --------------------------------------------------------------------------- #
# Status guards (2.3.3)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_progress_is_refused_before_the_job_is_claimed(cur, queue):
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    cur.execute(f"SELECT msg_id FROM public.{queue.table} WHERE id = %s", (job_id,))
    msg_id = cur.fetchone()[0]
    cur.execute(f"SELECT public.{queue.progress}(%s, %s, 'encoding', 1, 2, 300)", (job_id, msg_id))
    assert cur.fetchone()[0] is False


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
@pytest.mark.parametrize("verb", ["complete", "fail"])
def test_settling_is_refused_before_the_job_is_claimed(cur, queue, verb):
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    cur.execute(f"SELECT msg_id FROM public.{queue.table} WHERE id = %s", (job_id,))
    msg_id = cur.fetchone()[0]
    if verb == "complete":
        cur.execute(f"SELECT public.{queue.complete}(%s, %s, 'rendered')", (job_id, msg_id))
    else:
        cur.execute(f"SELECT public.{queue.fail}(%s, %s, 'boom', 'boom')", (job_id, msg_id))
    assert cur.fetchone()[0] is False
    assert _status(cur, queue, job_id) == "queued"


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
@pytest.mark.parametrize("verb", ["progress", "complete", "fail"])
def test_settled_jobs_cannot_be_moved_again(cur, queue, verb):
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    claimed = _claim(cur, queue)
    msg_id = claimed[queue.claim_cols.index("msg_id")]
    cur.execute(f"SELECT public.{queue.complete}(%s, %s, 'rendered')", (job_id, msg_id))
    assert cur.fetchone()[0] is True

    if verb == "progress":
        cur.execute(f"SELECT public.{queue.progress}(%s, %s, 'encoding', 1, 2, 300)", (job_id, msg_id))
    elif verb == "complete":
        cur.execute(f"SELECT public.{queue.complete}(%s, %s, 'kept')", (job_id, msg_id))
    else:
        cur.execute(f"SELECT public.{queue.fail}(%s, %s, 'boom', 'boom')", (job_id, msg_id))
    assert cur.fetchone()[0] is False
    assert _status(cur, queue, job_id) == "rendered"


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_settling_requires_the_matching_message(cur, queue):
    """A mismatched pair would otherwise destroy an unrelated message."""
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    claimed = _claim(cur, queue)
    msg_id = claimed[queue.claim_cols.index("msg_id")]
    cur.execute(f"SELECT public.{queue.complete}(%s, %s, 'rendered')", (job_id, msg_id + 1000))
    assert cur.fetchone()[0] is False
    assert _status(cur, queue, job_id) == "rendering"


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_complete_rejects_an_outcome_that_is_not_rendered_or_kept(cur, queue):
    import psycopg

    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    claimed = _claim(cur, queue)
    msg_id = claimed[queue.claim_cols.index("msg_id")]
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(f"SELECT public.{queue.complete}(%s, %s, 'finished')", (job_id, msg_id))


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
@pytest.mark.parametrize("outcome", ["rendered", "kept"])
def test_complete_records_the_outcome_and_drops_the_message(cur, queue, outcome):
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    claimed = _claim(cur, queue)
    msg_id = claimed[queue.claim_cols.index("msg_id")]
    depth = _queue_depth(cur, queue)
    cur.execute(f"SELECT public.{queue.complete}(%s, %s, %s)", (job_id, msg_id, outcome))
    assert cur.fetchone()[0] is True
    assert _status(cur, queue, job_id) == outcome
    assert _queue_depth(cur, queue) == depth - 1


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_fail_records_the_code_and_archives_the_message(cur, queue):
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    claimed = _claim(cur, queue)
    msg_id = claimed[queue.claim_cols.index("msg_id")]
    depth, archived = _queue_depth(cur, queue), _archive_depth(cur, queue)
    cur.execute(f"SELECT public.{queue.fail}(%s, %s, 'unusable', 'no frames')", (job_id, msg_id))
    assert cur.fetchone()[0] is True
    cur.execute(f"SELECT status, error_code, error FROM public.{queue.table} WHERE id = %s", (job_id,))
    assert cur.fetchone() == ("failed", "unusable", "no frames")
    assert _queue_depth(cur, queue) == depth - 1
    assert _archive_depth(cur, queue) == archived + 1


# --------------------------------------------------------------------------- #
# Redelivery (2.3.4)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_a_message_is_redelivered_once_its_visibility_lapses(cur, queue):
    """This is how a job whose worker died is picked up again."""
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    first = _claim(cur, queue, vt=0)
    assert first is not None
    second = _claim(cur, queue, vt=0)
    assert second is not None
    assert second[queue.claim_cols.index("job_id")] == job_id


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_progress_pushes_the_visibility_forward(cur, queue):
    """A live worker keeps its job for as long as it reports."""
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    claimed = _claim(cur, queue, vt=0)
    msg_id = claimed[queue.claim_cols.index("msg_id")]
    cur.execute(f"SELECT vt FROM pgmq.q_{queue.name} WHERE msg_id = %s", (msg_id,))
    before = cur.fetchone()[0]
    cur.execute(f"SELECT public.{queue.progress}(%s, %s, 'encoding', 1, 2, 600)", (job_id, msg_id))
    assert cur.fetchone()[0] is True
    cur.execute(f"SELECT vt FROM pgmq.q_{queue.name} WHERE msg_id = %s", (msg_id,))
    assert cur.fetchone()[0] > before
    assert _claim(cur, queue, vt=0) is None, "a renewed message must not be redelivered"


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_a_job_fails_as_redelivered_past_the_maximum_deliveries(cur, queue):
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    for _ in range(2):
        assert _claim(cur, queue, vt=0, max_reads=2) is not None
    archived = _archive_depth(cur, queue)

    assert _claim(cur, queue, vt=0, max_reads=2) is None, "a poison message must not be handed out"
    cur.execute(f"SELECT status, error_code FROM public.{queue.table} WHERE id = %s", (job_id,))
    assert cur.fetchone() == ("failed", "redelivered")
    assert _archive_depth(cur, queue) == archived + 1
    assert _queue_depth(cur, queue) == 0


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_a_settled_job_with_a_stray_message_is_archived_not_rerun(cur, queue):
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    claimed = _claim(cur, queue, vt=0)
    msg_id = claimed[queue.claim_cols.index("msg_id")]
    cur.execute(f"SELECT public.{queue.complete}(%s, %s, 'rendered')", (job_id, msg_id))
    # Put the message back as if the delete had not reached the queue.
    cur.execute(
        "SELECT pgmq.send(%s, jsonb_build_object('job_id', %s::text))", (queue.name, str(job_id))
    )
    assert _claim(cur, queue, vt=0) is None
    assert _status(cur, queue, job_id) == "rendered"

# --------------------------------------------------------------------------- #
# Input guards
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
@pytest.mark.parametrize("bad_vt", [3601, 2147483647, -1, None])
def test_claim_refuses_an_out_of_range_visibility_timeout(cur, queue, bad_vt):
    """An unbounded timeout strands the job: the message stays invisible while the row
    stays active, so every later request for that item returns the stranded job."""
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation, match="p_vt must be between"):
        cur.execute(f"SELECT * FROM public.{queue.claim}(%s, 3)", (bad_vt,))


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
@pytest.mark.parametrize("bad_vt", [3601, 2147483647, -1, None])
def test_progress_refuses_an_out_of_range_visibility_timeout(cur, queue, bad_vt):
    import psycopg

    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    claimed = _claim(cur, queue)
    msg_id = claimed[queue.claim_cols.index("msg_id")]
    with pytest.raises(psycopg.errors.CheckViolation, match="p_vt must be between"):
        cur.execute(
            f"SELECT public.{queue.progress}(%s, %s, 'encoding', 1, 2, %s)",
            (job_id, msg_id, bad_vt),
        )


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
@pytest.mark.parametrize("bad_max", [0, -1, None])
def test_claim_refuses_a_delivery_limit_below_one(cur, queue, bad_max):
    """Below 1 the first delivery is already past the limit, so the first claim
    dead-letters the job instead of handing it out."""
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation, match="p_max_reads must be at least 1"):
        cur.execute(f"SELECT * FROM public.{queue.claim}(0, %s)", (bad_max,))


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_a_rejected_claim_leaves_the_job_queued(cur, queue):
    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    cur.execute("SAVEPOINT before_bad_claim")
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(f"SELECT * FROM public.{queue.claim}(%s, 3)", (999999,))
    cur.execute("ROLLBACK TO SAVEPOINT before_bad_claim")
    assert _status(cur, queue, job_id) == "queued"
    assert _queue_depth(cur, queue) == 1


@pytest.mark.parametrize("queue", QUEUES, ids=IDS)
def test_complete_refuses_a_null_outcome(cur, queue):
    """NULL NOT IN (...) is NULL, so without an explicit check the guard is skipped and
    the UPDATE aborts the caller's transaction on the status constraint instead."""
    import psycopg

    args = _target(cur, queue)
    job_id, _ = _enqueue(cur, queue, args)
    claimed = _claim(cur, queue)
    msg_id = claimed[queue.claim_cols.index("msg_id")]
    with pytest.raises(psycopg.errors.CheckViolation, match="outcome must be rendered or kept"):
        cur.execute(f"SELECT public.{queue.complete}(%s, %s, NULL)", (job_id, msg_id))


def test_a_scan_whose_chain_is_broken_cannot_be_refiled_under_another_experiment(cur):
    """With no resolvable scan -> plant -> wave -> experiment chain the active job's
    experiment is all there is to go on, so a request naming a different one is a
    disagreement rather than a duplicate."""
    import psycopg

    cur.execute("INSERT INTO cyl_experiments (name) VALUES ('queue-test-a') RETURNING id")
    first_experiment = cur.fetchone()[0]
    cur.execute("INSERT INTO cyl_experiments (name) VALUES ('queue-test-b') RETURNING id")
    other_experiment = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days) "
        "VALUES (NULL, now(), 1) RETURNING id"
    )
    scan_id = cur.fetchone()[0]

    cur.execute(
        "SELECT job_id, created FROM public.enqueue_cyl_video(%s, %s, NULL)",
        (scan_id, first_experiment),
    )
    job_id, created = cur.fetchone()
    assert created is True

    cur.execute("SAVEPOINT before_disagreement")
    with pytest.raises(psycopg.errors.CheckViolation, match="has an active video job under"):
        cur.execute(
            "SELECT job_id, created FROM public.enqueue_cyl_video(%s, %s, NULL)",
            (scan_id, other_experiment),
        )
    cur.execute("ROLLBACK TO SAVEPOINT before_disagreement")

    cur.execute(
        "SELECT job_id, created FROM public.enqueue_cyl_video(%s, %s, NULL)",
        (scan_id, first_experiment),
    )
    same_job, created_again = cur.fetchone()
    assert created_again is False and same_job == job_id
    cur.execute("SELECT experiment_id FROM public.cyl_video_jobs WHERE id = %s", (job_id,))
    assert cur.fetchone()[0] == first_experiment
