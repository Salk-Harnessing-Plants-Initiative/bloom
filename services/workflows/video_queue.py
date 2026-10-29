"""The two video queues and their wrappers.

Everything goes through the SECURITY DEFINER wrappers (enqueue / claim / report
progress / complete / fail), called as the bloom_workflows app user, in the
style of pipeline_queue.py. The job row is what the pages read; the pgmq message
carries only its id. The loop that consumes them is video_worker_loop.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from video_writer import ENCODE_TIMEOUT_SECONDS

# Encoding is the one stage that reports nothing while it runs, so the timeout
# outlives the longest encode plus the upload and recording after it.
UPLOAD_MARGIN_SECONDS = 180
VISIBILITY_TIMEOUT_SECONDS = int(ENCODE_TIMEOUT_SECONDS) + UPLOAD_MARGIN_SECONDS
# A job whose render kills its worker this many times is failed as 'redelivered'.
MAX_READS = 3

# Outcomes complete accepts.
RENDERED = "rendered"
KEPT = "kept"
# Anything a render raises that its worker does not name.
UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class Queue:
    name: str
    enqueue: str
    claim: str
    progress: str
    complete: str
    fail: str


PLATE_QUEUE = Queue(
    name="gravi_plate_video",
    enqueue="enqueue_gravi_plate_video",
    claim="claim_gravi_plate_video_job",
    progress="report_gravi_plate_video_progress",
    complete="complete_gravi_plate_video_job",
    fail="fail_gravi_plate_video_job",
)

CYL_QUEUE = Queue(
    name="cyl_video_generation",
    enqueue="enqueue_cyl_video",
    claim="claim_cyl_video_job",
    progress="report_cyl_video_progress",
    complete="complete_cyl_video_job",
    fail="fail_cyl_video_job",
)


class RenderFailed(Exception):
    """A render that ended without a video, with the code the page shows.

    `error` is the operator's text: it is stored in a column signed-in roles
    cannot read, and logged.
    """

    def __init__(self, code: str, error: str):
        super().__init__(f"{code}: {error}")
        self.code = code
        self.error = error


# --- wrappers ----------------------------------------------------------------


def _first(rows):
    return rows[0] if rows else None


def enqueue(client, queue: Queue, params: dict) -> dict:
    """The item's active job, created if there was none: {job_id, created}."""
    row = _first(client.rpc(queue.enqueue, params).execute().data)
    if not row or not row.get("job_id"):
        raise RuntimeError(f"{queue.enqueue} returned no job")
    return {"job_id": row["job_id"], "created": bool(row.get("created"))}


def claim(client, queue: Queue) -> dict | None:
    """The next job, or None when the queue is empty or the message was dead-lettered."""
    rows = (
        client.rpc(
            queue.claim,
            {"p_vt": VISIBILITY_TIMEOUT_SECONDS, "p_max_reads": MAX_READS},
        )
        .execute()
        .data
    )
    return _first(rows)


def report(client, queue: Queue, job: dict, stage: str, done, total) -> bool:
    """Record progress and renew the job's visibility. False when the job is no longer ours."""
    return bool(
        client.rpc(
            queue.progress,
            {
                "p_job_id": job["job_id"],
                "p_msg_id": job["msg_id"],
                "p_stage": stage,
                "p_frames_done": done,
                "p_frames_total": total,
                "p_vt": VISIBILITY_TIMEOUT_SECONDS,
            },
        )
        .execute()
        .data
    )


def complete(client, queue: Queue, job: dict, outcome: str) -> bool:
    return bool(
        client.rpc(
            queue.complete,
            {
                "p_job_id": job["job_id"],
                "p_msg_id": job["msg_id"],
                "p_outcome": outcome,
            },
        )
        .execute()
        .data
    )


def fail(client, queue: Queue, job: dict, code: str, error: str) -> bool:
    return bool(
        client.rpc(
            queue.fail,
            {
                "p_job_id": job["job_id"],
                "p_msg_id": job["msg_id"],
                "p_error_code": code,
                "p_error": error,
            },
        )
        .execute()
        .data
    )
