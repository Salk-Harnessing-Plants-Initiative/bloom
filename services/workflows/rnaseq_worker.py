"""
RNA-seq dispatch worker.

Claims the next queued run of any type from the shared rnaseq_dispatch queue, builds
its Argo Workflow with the entry for its workflow_type in rnaseq_workflows, submits it
through k8s_client, and records it as submitted or failed. Runs as the bloom_workflows
app user.

Deploy: a container off the workflows image with `command: python rnaseq_worker.py`.

Env:
    WORKFLOWS_WORKER_POLL_SECONDS  idle sleep when the queue is empty (default 5)
    WORKFLOWS_DISPATCH_VT_SECONDS  seconds a claimed run is hidden (default 60)
    WORKFLOWS_DISPATCH_MAX_READS   deliveries before a run is failed (default 5)
    WORKFLOWS_K8S_PIPELINE_SECRET_NAME  Secret added to every workflow as the optional
                                   bloom-credentials volume; unset gives an empty folder
"""

import logging
import os
import signal
import time

import k8s_client
from k8s_client import (
    K8sAlreadyExistsError,
    K8sConfigError,
    K8sSubmissionError,
    submit_workflow,
)
from pipeline_queue import MAX_READS, VISIBILITY_TIMEOUT
from rnaseq_workflows import CLAIM_FN, COMPLETE_FN, FAIL_FN, WORKFLOW_TYPES
from supabase_client import SINGLE_ROW_RPC_TIMEOUT_SECONDS
from supabase_client import app_client as _app_client

logger = logging.getLogger(__name__)

POLL_INTERVAL = float(os.environ.get("WORKFLOWS_WORKER_POLL_SECONDS", "5"))

# Shown to users in the run's message; the detail is only in this service's log.
SUBMISSION_FAILED = "Argo Workflow submission failed"

_PIPELINE_SECRET_ENV = "WORKFLOWS_K8S_PIPELINE_SECRET_NAME"

_running = True


def app_client():
    """Every call here is a small single-row RPC, so a tight timeout is safe."""
    return _app_client(timeout_seconds=SINGLE_ROW_RPC_TIMEOUT_SECONDS)


def _stop(signum, _frame):
    """Stops after the run in progress; never interrupts a submission."""
    global _running
    logger.info("rnaseq_worker: received signal %s, stopping after this run", signum)
    _running = False


def _claim(client) -> dict | None:
    rows = (
        client.rpc(CLAIM_FN, {"p_vt": VISIBILITY_TIMEOUT, "p_max_reads": MAX_READS})
        .execute()
        .data
    )
    return rows[0] if rows else None


def _complete(client, run: dict, workflow_name: str) -> bool:
    """Returns False if the run had already moved past queued."""
    return (
        client.rpc(
            COMPLETE_FN,
            {
                "p_run_id": run["run_id"],
                "p_msg_id": run["msg_id"],
                "p_argo_workflow_name": workflow_name,
            },
        )
        .execute()
        .data
    )


def _fail(client, run: dict, message: str) -> bool:
    """Returns False if the run had already moved past queued."""
    return (
        client.rpc(
            FAIL_FN,
            {
                "p_run_id": run["run_id"],
                "p_msg_id": run["msg_id"],
                "p_message": message,
            },
        )
        .execute()
        .data
    )


def _fail_logged(client, run: dict, message: str) -> None:
    """Records a failure; if that call errors, the run comes back later."""
    try:
        _fail(client, run, message)
    except Exception as exc:
        logger.error(
            "rnaseq_worker: run %s failed (%s) and recording it also failed; "
            "it will come back: %s",
            run["run_id"],
            message,
            exc,
        )


def process_one(client) -> bool:
    """Claim and dispatch one run. Returns True if a run was claimed."""
    try:
        run = _claim(client)
    except Exception as exc:
        logger.warning("rnaseq_worker: claim failed: %s", exc)
        return False
    if not run:
        return False

    run_id = run["run_id"]
    wf = WORKFLOW_TYPES.get(run["workflow_type"])
    if wf is None:
        # A type the database accepts but this worker version does not know.
        logger.error(
            "rnaseq_worker: run %s has unhandled workflow type %r",
            run_id,
            run["workflow_type"],
        )
        _fail_logged(
            client, run, f"workflow type {run['workflow_type']!r} is not supported"
        )
        return True
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
        _fail_logged(client, run, SUBMISSION_FAILED)
        return True

    # The Workflow exists now, so an error here must never mark the run failed.
    try:
        if _complete(client, run, workflow_name):
            logger.info(
                "rnaseq_worker: submitted %s run %s as %s",
                wf.name,
                run_id,
                workflow_name,
            )
        else:
            logger.warning(
                "rnaseq_worker: %s run %s was submitted as %s but had already "
                "moved past queued",
                wf.name,
                run_id,
                workflow_name,
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


def _warn_if_pipeline_secret_invalid():
    """A set but invalid Secret name becomes an empty folder, so steps upload nothing."""
    if os.environ.get(_PIPELINE_SECRET_ENV) and not k8s_client.PIPELINE_SECRET_NAME:
        logger.warning(
            "rnaseq_worker: %s %s; workflows get an empty bloom-credentials folder, "
            "so their steps won't upload logs",
            _PIPELINE_SECRET_ENV,
            k8s_client.PIPELINE_SECRET_NAME_INVALID,
        )


def run():
    _warn_if_pipeline_secret_invalid()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    client = _connect_with_retry()
    if client is None:
        logger.info("rnaseq worker stopped before connecting")
        return
    logger.info(
        "rnaseq worker started (poll=%ss, types=%s)",
        POLL_INTERVAL,
        ", ".join(WORKFLOW_TYPES),
    )
    while _running:
        try:
            handled = process_one(client)
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
