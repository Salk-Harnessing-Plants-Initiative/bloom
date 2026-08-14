"""
Pipeline dispatch worker (bloom #11 Phase 2).

Polls the pgmq `cyl_pipeline_dispatch` queue (via the claim wrapper), submits
each claimed batch to Argo as a Kubernetes Workflow CRD via k8s_client, and
completes or fails the batch. Runs as the least-privilege bloom_workflows app
user — no direct DB connection. Modeled directly on bloom PR #469's (unmerged)
`worker.py`: same claim/complete/fail loop shape, graceful shutdown, and
reconnect-on-error behavior — different middle step (submit_workflow instead
of generating a video).

Deploy: a container off the workflows image with `command: python
dispatch_worker.py`.

Env:
    WORKFLOWS_WORKER_POLL_SECONDS  idle sleep between empty polls (default 5)
"""

import logging
import os
import signal
import time

from k8s_client import (
    K8sConfigError,
    K8sSubmissionError,
    build_workflow_body,
    submit_workflow,
)
from pipeline_queue import claim_batch, complete_batch, fail_batch
from supabase_client import app_client

logger = logging.getLogger(__name__)

POLL_INTERVAL = float(os.environ.get("WORKFLOWS_WORKER_POLL_SECONDS", "5"))

_running = True


def _stop(signum, _frame):
    """SIGTERM/SIGINT -> flip the running-flag only. Does not interrupt an
    in-flight process_one() call, so a mid-submission batch's complete/fail
    call always finishes; the loop simply doesn't start a new claim after."""
    global _running
    logger.info(
        "dispatch_worker: received signal %s, shutting down after current batch",
        signum,
    )
    _running = False


def process_one(client) -> bool:
    """Claim and process a single batch. Returns True if a batch was handled
    (including a K8sConfigError, which is a no-op left for redelivery)."""
    try:
        batch = claim_batch(client)
    except Exception as exc:
        logger.warning(
            "dispatch_worker: claim failed (a claimed batch may await redelivery): %s",
            exc,
        )
        return False
    if not batch:
        return False

    run_id = batch["run_id"]
    batch_index = batch["batch_index"]
    scan_ids = batch["scan_ids"]
    msg_id = batch["msg_id"]
    logger.info(
        "dispatch_worker: claimed run %s batch %s (%s scans)",
        run_id,
        batch_index,
        len(scan_ids),
    )

    try:
        body = build_workflow_body(run_id, batch_index, scan_ids)
        workflow_name = submit_workflow(body)
    except K8sConfigError as exc:
        # A service misconfiguration, not a genuine submission attempt — leave
        # the claim unsettled so it's reclaimable once fixed (via the
        # visibility timeout), rather than permanently failing real scans
        # over a deploy/ops mistake.
        logger.error(
            "dispatch_worker: K8s not configured, leaving run %s batch %s unsettled: %s",
            run_id,
            batch_index,
            exc,
        )
        return True
    except K8sSubmissionError as exc:
        logger.warning(
            "dispatch_worker: run %s batch %s submission failed: %s",
            run_id,
            batch_index,
            exc,
        )
        try:
            fail_batch(client, run_id, batch_index, msg_id, scan_ids, str(exc))
        except Exception as fail_exc:
            # Symmetric with the complete_batch handling below: a failed
            # fail_batch RPC just leaves the claim for redelivery (its own
            # visibility timeout), it must not propagate to run()'s generic
            # loop handler and lose this batch's run/batch-id context.
            logger.error(
                "dispatch_worker: run %s batch %s submission failed and the "
                "fail RPC also errored; leaving for redelivery: %s",
                run_id,
                batch_index,
                fail_exc,
            )
        return True

    # The Workflow is already submitted — a failed/lost completion RPC must
    # NOT run fail_batch: that would mark a real, running submission as
    # failed. Log instead; if the completion never committed, the message
    # redelivers after its visibility timeout and is re-completed idempotently.
    try:
        complete_batch(client, run_id, batch_index, msg_id, scan_ids, workflow_name)
        logger.info(
            "dispatch_worker: submitted run %s batch %s as %s",
            run_id,
            batch_index,
            workflow_name,
        )
    except Exception as exc:
        logger.error(
            "dispatch_worker: run %s batch %s submitted (workflow %s) but "
            "completion RPC errored; not failing the batch — leaving for "
            "redelivery/idempotent completion: %s",
            run_id,
            batch_index,
            workflow_name,
            exc,
        )
    return True


def _connect_with_retry():
    """Retry app_client() with backoff until it succeeds or a shutdown signal
    arrives. A transient Supabase outage at container startup must not crash
    the process — Docker's `restart: unless-stopped` would just crash-loop it
    forever, unlike the in-loop reconnect below, which already retries."""
    client = None
    while client is None and _running:
        try:
            client = app_client()
        except Exception as exc:
            logger.error(
                "dispatch_worker: could not connect on startup, retrying in %ss: %s",
                POLL_INTERVAL,
                exc,
            )
            time.sleep(POLL_INTERVAL)
    return client


def run():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    client = _connect_with_retry()
    if client is None:
        logger.info("dispatch worker stopped before connecting")
        return
    logger.info("dispatch worker started (poll=%ss)", POLL_INTERVAL)
    while _running:
        try:
            handled = process_one(client)
        except Exception as exc:
            # A loop-level error (e.g. an expired session) — reconnect and retry.
            logger.exception("dispatch_worker: loop error, reconnecting: %s", exc)
            time.sleep(POLL_INTERVAL)
            try:
                client = app_client()
            except Exception as reconnect_exc:
                # Reconnect itself failed (e.g. a sustained Supabase outage) —
                # log it. Without this, an extended outage produces exactly
                # one log line ever, then silence every POLL_INTERVAL while
                # the loop keeps retrying with a stale client.
                logger.error(
                    "dispatch_worker: reconnect failed, will retry: %s",
                    reconnect_exc,
                )
            continue
        if not handled:
            time.sleep(POLL_INTERVAL)
    logger.info("dispatch worker stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    run()
