"""
Queued cyl-scan video worker.

Polls the pgmq 'cyl_video_generation' queue (via the claim wrapper), generates the
video with the SAME code the on-demand route uses (video.generate_scan_video),
records the result, and deletes or dead-letters the message. Runs as the
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

from supabase_client import app_client
from video import generate_scan_video
from video_queue import claim_job, complete_job, fail_job

logger = logging.getLogger(__name__)

POLL_INTERVAL = float(os.environ.get("WORKFLOWS_WORKER_POLL_SECONDS", "5"))

_running = True


def _stop(signum, _frame):
    """SIGTERM/SIGINT → finish the in-flight job, then exit the loop."""
    global _running
    logger.info("worker: received signal %s, shutting down after current job", signum)
    _running = False


def process_one(client) -> bool:
    """Claim and process a single job. Returns True if a job was handled."""
    job = claim_job(client)
    if not job:
        return False

    job_id, scan_id, msg_id = job["job_id"], job["scan_id"], job["msg_id"]
    logger.info("worker: claimed job %s (scan %s)", job_id, scan_id)
    try:
        result = generate_scan_video(client, scan_id)
        complete_job(client, job_id, msg_id, result["path"])
        logger.info("worker: completed job %s (%s frames)", job_id, result.get("frames"))
    except Exception as exc:
        # generate_scan_video raises HTTPException on encode failures; anything
        # else is unexpected. Either way, record it and let retry/dead-letter run.
        detail = getattr(exc, "detail", exc)
        logger.warning("worker: job %s failed: %s", job_id, detail)
        fail_job(client, job_id, msg_id, detail)
    return True


def run():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    client = app_client()
    logger.info(
        "cyl-video worker started (poll=%ss)", POLL_INTERVAL
    )
    while _running:
        try:
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
