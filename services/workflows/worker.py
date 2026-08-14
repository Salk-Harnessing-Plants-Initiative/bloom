"""
Queued cyl-scan video worker.

Polls the pgmq 'cyl_video_generation' queue (via the claim wrapper), generates the
video with the SAME orchestration the on-demand route uses
(video.generate_experiment_scan_video — validates + encodes + records
cyl_scan_videos), and completes or dead-letters the message. Runs as the
least-privilege bloom_workflows app user — no direct DB connection.

Deploy: a container off the workflows image with `command: python worker.py`,
CPU-capped, scaled by replicas (pgmq's claim is concurrency-safe).

Env:
    WORKFLOWS_WORKER_POLL_SECONDS  idle sleep between empty polls (default 5)
    WORKFLOWS_WORKER_VT_SECONDS    per-message visibility timeout (default 120)
"""

import logging
import os
import signal
import time

from fastapi import HTTPException

from supabase_client import app_client
from video import generate_experiment_scan_video
from video_queue import claim_job, complete_job, fail_job, queue_stats

logger = logging.getLogger(__name__)

POLL_INTERVAL = float(os.environ.get("WORKFLOWS_WORKER_POLL_SECONDS", "5"))

# How often the backlog is reported while it is non-empty. The high-water mark is logged the
# moment it moves, regardless.
DEPTH_LOG_INTERVAL = float(os.environ.get("WORKFLOWS_WORKER_DEPTH_LOG_SECONDS", "60"))

_running = True

# Deepest backlog this process has seen. Nothing else records it: a queue that was 400 deep an
# hour ago and is empty now looks identical to one that was never used.
_max_depth_seen = 0
_last_depth_log = 0.0


def _stop(signum, _frame):
    """SIGTERM/SIGINT → finish the in-flight job, then exit the loop."""
    global _running
    logger.info("worker: received signal %s, shutting down after current job", signum)
    _running = False


def _safe_detail(exc: Exception) -> str:
    """A user-facing failure reason for cyl_video_jobs.error (readable by users).

    HTTPException.detail is curated/generic by design; any other exception (e.g. a raw
    Storage/ffmpeg error) is replaced with a generic message so internal paths/stderr
    don't leak into the user-readable column.
    """
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return "video generation failed (internal error)"


def log_backlog(client, now=time.monotonic) -> None:
    """Report the backlog, and its high-water mark whenever that moves.

    Never raises: this is instrumentation, and a failed stats call must not cost a job.
    """
    global _max_depth_seen, _last_depth_log
    try:
        stats = queue_stats(client)
    except Exception as exc:
        logger.debug("worker: queue stats unavailable: %s", exc)
        return

    depth = stats["queued"] + stats["processing"]
    moment = now()
    if depth > _max_depth_seen:
        _max_depth_seen = depth
        logger.info("worker: queue depth high-water mark %s", depth)
    elif not depth or moment - _last_depth_log < DEPTH_LOG_INTERVAL:
        return

    _last_depth_log = moment
    oldest = stats["oldest_queued_seconds"]
    logger.info(
        "worker: queue depth %s (queued %s, processing %s), oldest queued %s, max seen %s",
        depth,
        stats["queued"],
        stats["processing"],
        f"{oldest:.0f}s" if oldest is not None else "n/a",
        _max_depth_seen,
    )


def process_one(client) -> bool:
    """Claim and process a single job. Returns True if a job was handled."""
    # Claiming is its own step: the RPC commits server-side before the response is read, so a
    # lost response leaves a job claimed that this worker never learned the id of. It cannot be
    # failed here — there is no job_id to fail — but it is worth its own line rather than
    # arriving in run() indistinguishable from an expired session. The message reappears when
    # its visibility timeout lapses.
    try:
        job = claim_job(client)
    except Exception as exc:
        logger.warning(
            "worker: claim failed (a claimed job may await redelivery): %s", exc
        )
        return False
    if not job:
        return False

    job_id, scan_id, msg_id = job["job_id"], job["scan_id"], job["msg_id"]
    experiment_id = job["experiment_id"]
    logger.info(
        "worker: claimed job %s (experiment %s scan %s)", job_id, experiment_id, scan_id
    )

    # 1. Render — via the same orchestration the on-demand route uses, so cyl_scan_videos is
    #    recorded (not the low-level encoder). A failure here is a real generation failure.
    try:
        result = generate_experiment_scan_video(experiment_id, scan_id, client=client)
    except Exception as exc:
        detail = _safe_detail(exc)
        logger.warning("worker: job %s generation failed: %s", job_id, detail)
        fail_job(client, job_id, msg_id, detail)
        return True

    # 2. Complete — the video is already written. A failed/lost completion RPC must NOT run
    #    fail_job: that would set an already-done job back to 'queued' and archive a deleted
    #    message, stranding it forever (claim only reads the live queue). Log instead; if the
    #    completion never committed the message redelivers after its visibility timeout and is
    #    re-completed idempotently.
    try:
        complete_job(client, job_id, msg_id, result["path"])
        logger.info(
            "worker: completed job %s (%s frames)", job_id, result.get("frames")
        )
    except Exception as exc:
        logger.error(
            "worker: job %s render succeeded (video written) but completion RPC errored; "
            "not failing the job — leaving for redelivery/idempotent completion: %s",
            job_id,
            exc,
        )
    return True


def run():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    client = app_client()
    logger.info("cyl-video worker started (poll=%ss)", POLL_INTERVAL)
    while _running:
        try:
            log_backlog(client)
            handled = process_one(client)
        except Exception as exc:
            # A loop-level error (e.g. an expired session) — reconnect and retry.
            logger.exception("worker: loop error, reconnecting: %s", exc)
            time.sleep(POLL_INTERVAL)
            try:
                client = app_client()
            except Exception:
                pass
            continue
        if not handled:
            time.sleep(POLL_INTERVAL)
    logger.info("cyl-video worker stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    run()
