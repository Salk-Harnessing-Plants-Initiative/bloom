"""The two video queues' wrappers, and the claim loop both video workers run.

Everything goes through the SECURITY DEFINER wrappers (enqueue / claim / report
progress / complete / fail), called as the bloom_workflows app user, in the
style of pipeline_queue.py. The job row is what the pages read; the pgmq message
carries only its id.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from video_writer import ENCODE_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# The stages a job reports. The web pins these literals in its own tests.
STAGE_DOWNLOADING = "downloading"
STAGE_ENCODING = "encoding"
STAGE_UPLOADING = "uploading"

# Encoding is the one stage that reports nothing while it runs, so the timeout
# outlives the longest encode plus the upload and recording after it.
UPLOAD_MARGIN_SECONDS = 180
VISIBILITY_TIMEOUT_SECONDS = int(ENCODE_TIMEOUT_SECONDS) + UPLOAD_MARGIN_SECONDS
# A job whose render kills its worker this many times is failed as 'redelivered'.
MAX_READS = 3
PROGRESS_INTERVAL_SECONDS = 1.0
POLL_SECONDS = float(os.environ.get("WORKFLOWS_VIDEO_POLL_SECONDS", "2"))

# Touched on every poll and progress report; the compose healthcheck reads its age.
HEARTBEAT_PATH = Path(
    os.environ.get("WORKFLOWS_VIDEO_HEARTBEAT", "/tmp/video-worker-heartbeat")
)
# Longer than the encode, the one stretch that touches nothing.
HEARTBEAT_STALE_SECONDS = 600

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


# --- progress ----------------------------------------------------------------


def beat() -> None:
    """Mark this worker alive for the healthcheck. Never fails a render."""
    try:
        HEARTBEAT_PATH.touch()
    except OSError as exc:
        logger.warning("could not touch the heartbeat at %s: %s", HEARTBEAT_PATH, exc)


class Progress:
    """A render's progress callback: at most once per interval, always on a stage change.

    A failed report is logged and dropped. Progress is advisory, and the render
    is worth more than the row's frame count; a lapse in renewal only risks a
    redelivery, which the next claim settles.
    """

    def __init__(
        self,
        send: Callable[[str, int | None, int | None], bool],
        *,
        clock: Callable[[], float] = time.monotonic,
        interval: float = PROGRESS_INTERVAL_SECONDS,
    ):
        self._send = send
        self._clock = clock
        self._interval = interval
        self._stage: str | None = None
        self._last: float | None = None
        self.lost = False

    def __call__(
        self, stage: str, done: int | None = None, total: int | None = None
    ) -> None:
        now = self._clock()
        if (
            stage == self._stage
            and self._last is not None
            and now - self._last < self._interval
        ):
            return
        self._stage = stage
        self._last = now
        beat()
        try:
            ours = self._send(stage, done, total)
        except Exception as exc:
            logger.warning("could not report progress (%s): %s", stage, exc)
            return
        if not ours and not self.lost:
            # Settled elsewhere, e.g. failed as redelivered; complete will say so too.
            logger.warning("progress refused: the job is no longer rendering")
            self.lost = True


# --- the claim loop ----------------------------------------------------------


Render = Callable[[object, dict, Progress], str]


class Worker:
    """Claim → render → complete or fail, one job at a time.

    On SIGTERM the current job finishes and no new one is claimed. A failed
    complete or fail call leaves the job for redelivery rather than guessing.
    """

    def __init__(
        self,
        queue: Queue,
        render: Render,
        client_factory: Callable[[], object],
        *,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.queue = queue
        self.render = render
        self.client_factory = client_factory
        self.sleep = sleep
        self.running = True

    def stop(self, signum=None, _frame=None) -> None:
        logger.info(
            "%s: signal %s, stopping after the current job", self.queue.name, signum
        )
        self.running = False

    def process_one(self, client) -> bool:
        """Claim and settle one job. True when a job was handled."""
        beat()
        # A claim error reaches run(), which reconnects: an expired session looks like this.
        job = claim(client, self.queue)
        if not job:
            return False

        job_id = job["job_id"]
        logger.info(
            "%s: claimed job %s (delivery %s)",
            self.queue.name,
            job_id,
            job.get("reads"),
        )
        progress = Progress(
            lambda stage, done, total: report(
                client, self.queue, job, stage, done, total
            )
        )

        try:
            outcome = self.render(client, job, progress)
        except RenderFailed as exc:
            code, error = exc.code, exc.error
            logger.warning(
                "%s: job %s failed (%s): %s", self.queue.name, job_id, code, error
            )
        except Exception as exc:
            code, error = UNEXPECTED, f"{type(exc).__name__}: {exc}"
            logger.exception("%s: job %s failed unexpectedly", self.queue.name, job_id)
        else:
            logger.info("%s: job %s %s", self.queue.name, job_id, outcome)
            self._settle(job_id, lambda: complete(client, self.queue, job, outcome))
            return True

        self._settle(job_id, lambda: fail(client, self.queue, job, code, error))
        return True

    def _settle(self, job_id, call: Callable[[], bool]) -> None:
        try:
            settled = call()
        except Exception as exc:
            logger.error(
                "%s: job %s could not be settled; leaving it for redelivery: %s",
                self.queue.name,
                job_id,
                exc,
            )
            return
        if not settled:
            logger.warning(
                "%s: job %s was already settled elsewhere", self.queue.name, job_id
            )

    def _connect(self):
        """Retry until connected or stopped, so a Supabase outage at start does not crash-loop."""
        while self.running:
            try:
                return self.client_factory()
            except Exception as exc:
                logger.error(
                    "%s: could not connect, retrying: %s", self.queue.name, exc
                )
                self.sleep(POLL_SECONDS)
        return None

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        client = self._connect()
        logger.info("%s worker started (poll=%ss)", self.queue.name, POLL_SECONDS)
        while self.running and client is not None:
            try:
                handled = self.process_one(client)
            except Exception as exc:
                logger.error("%s: claim failed, reconnecting: %s", self.queue.name, exc)
                self.sleep(POLL_SECONDS)
                client = self._connect()
                continue
            if not handled:
                self.sleep(POLL_SECONDS)
        logger.info("%s worker stopped", self.queue.name)
