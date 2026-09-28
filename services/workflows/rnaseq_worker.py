"""
RNA-seq dispatch worker.

For each workflow type in rnaseq_workflows.WORKFLOW_TYPES, claims the next queued run,
submits its Argo Workflow through k8s_client, and records the outcome with the type's
complete or fail function. Runs as the bloom_workflows app user.

Deploy: a container off the workflows image with `command: python rnaseq_worker.py`.

Env:
    WORKFLOWS_WORKER_POLL_SECONDS  idle sleep when no type had a run (default 5)
    WORKFLOWS_DISPATCH_VT_SECONDS  seconds a claimed run is hidden (default 60)
    WORKFLOWS_DISPATCH_MAX_READS   deliveries before a run is failed (default 5)
"""

import logging
import os
import signal
import time

from k8s_client import (
    K8sAlreadyExistsError,
    K8sConfigError,
    K8sSubmissionError,
    submit_workflow,
)
from pipeline_queue import MAX_READS, VISIBILITY_TIMEOUT
from rnaseq_workflows import WORKFLOW_TYPES, WorkflowType
from supabase_client import SINGLE_ROW_RPC_TIMEOUT_SECONDS
from supabase_client import app_client as _app_client

logger = logging.getLogger(__name__)

POLL_INTERVAL = float(os.environ.get("WORKFLOWS_WORKER_POLL_SECONDS", "5"))

# Shown to users in the run's message; the detail is only in this service's log.
SUBMISSION_FAILED = "Argo Workflow submission failed"

_running = True


def app_client():
    """Every call here is a small single-row RPC, so a tight timeout is safe."""
    return _app_client(timeout_seconds=SINGLE_ROW_RPC_TIMEOUT_SECONDS)


def _stop(signum, _frame):
    """Stops after the run in progress; never interrupts a submission."""
    global _running
    logger.info("rnaseq_worker: received signal %s, stopping after this run", signum)
    _running = False


def _claim(client, wf: WorkflowType) -> dict | None:
    rows = (
        client.rpc(wf.claim_fn, {"p_vt": VISIBILITY_TIMEOUT, "p_max_reads": MAX_READS})
        .execute()
        .data
    )
    return rows[0] if rows else None


def _complete(client, wf: WorkflowType, run: dict, workflow_name: str) -> None:
    client.rpc(
        wf.complete_fn,
        {
            "p_run_id": run["run_id"],
            "p_msg_id": run["msg_id"],
            "p_argo_workflow_name": workflow_name,
        },
    ).execute()


def _fail(client, wf: WorkflowType, run: dict, message: str) -> None:
    client.rpc(
        wf.fail_fn,
        {"p_run_id": run["run_id"], "p_msg_id": run["msg_id"], "p_message": message},
    ).execute()


def process_one(client, wf: WorkflowType) -> bool:
    """Claim and dispatch one run of `wf`. Returns True if a run was claimed."""
    try:
        run = _claim(client, wf)
    except Exception as exc:
        logger.warning("rnaseq_worker: %s claim failed: %s", wf.name, exc)
        return False
    if not run:
        return False

    run_id = run["run_id"]
    logger.info("rnaseq_worker: claimed %s run %s", wf.name, run_id)

    try:
        body = wf.build_body(run)
        workflow_name = submit_workflow(body)
    except K8sConfigError as exc:
        # A deploy mistake, not a bad run: leave it to come back once fixed.
        logger.error(
            "rnaseq_worker: K8s not configured, leaving %s run %s queued: %s",
            wf.name,
            run_id,
            exc,
        )
        return True
    except K8sAlreadyExistsError:
        # An earlier attempt submitted it but never recorded it.
        workflow_name = body["metadata"]["name"]
        logger.info(
            "rnaseq_worker: %s run %s was already submitted as %s",
            wf.name,
            run_id,
            workflow_name,
        )
    except K8sSubmissionError as exc:
        logger.warning(
            "rnaseq_worker: %s run %s submission failed: %s", wf.name, run_id, exc
        )
        try:
            _fail(client, wf, run, SUBMISSION_FAILED)
        except Exception as fail_exc:
            logger.error(
                "rnaseq_worker: %s run %s failed and recording it also failed; "
                "it will come back: %s",
                wf.name,
                run_id,
                fail_exc,
            )
        return True

    # The Workflow exists now, so an error here must never mark the run failed.
    try:
        _complete(client, wf, run, workflow_name)
        logger.info(
            "rnaseq_worker: submitted %s run %s as %s", wf.name, run_id, workflow_name
        )
    except Exception as exc:
        logger.error(
            "rnaseq_worker: %s run %s submitted as %s but recording it failed; "
            "it will come back and be recorded then: %s",
            wf.name,
            run_id,
            workflow_name,
            exc,
        )
    return True


def process_all(client) -> bool:
    """One pass over every workflow type. Returns True if any run was claimed."""
    handled = False
    for wf in WORKFLOW_TYPES:
        if not _running:
            break
        handled = process_one(client, wf) or handled
    return handled


def _connect_with_retry():
    """Keeps retrying a Supabase connection at startup instead of crash-looping."""
    client = None
    while client is None and _running:
        try:
            client = app_client()
        except Exception as exc:
            logger.error(
                "rnaseq_worker: could not connect on startup, retrying in %ss: %s",
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
        logger.info("rnaseq worker stopped before connecting")
        return
    logger.info(
        "rnaseq worker started (poll=%ss, types=%s)",
        POLL_INTERVAL,
        ", ".join(wf.name for wf in WORKFLOW_TYPES),
    )
    while _running:
        try:
            handled = process_all(client)
        except Exception as exc:
            logger.exception("rnaseq_worker: loop error, reconnecting: %s", exc)
            time.sleep(POLL_INTERVAL)
            try:
                client = app_client()
            except Exception as reconnect_exc:
                logger.error(
                    "rnaseq_worker: reconnect failed, will retry: %s", reconnect_exc
                )
            continue
        if not handled:
            time.sleep(POLL_INTERVAL)
    logger.info("rnaseq worker stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    run()
