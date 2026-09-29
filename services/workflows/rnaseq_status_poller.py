"""
RNA-seq status poller.

Every WORKFLOWS_STATUS_POLL_SECONDS, reads the Argo Workflow of each submitted or running
rnaseq_runs row, turns it into the run's status with the reader for its workflow type in
rnaseq_workflows, and records it with update_rnaseq_run_status, which only moves a run
forward and writes nothing for an unchanged report. A Workflow that no longer exists
fails its run. Runs as the bloom_workflows app user; one poller per environment.

Deploy: a container off the workflows image with `command: python rnaseq_status_poller.py`.
"""

import logging
import os
import signal
import time

from k8s_client import K8sConfigError, get_workflow
from rnaseq_status import RunStatus
from rnaseq_workflows import WORKFLOW_TYPES
from supabase_client import SINGLE_ROW_RPC_TIMEOUT_SECONDS
from supabase_client import app_client as _app_client

logger = logging.getLogger(__name__)

RUNS_TABLE = "rnaseq_runs"
UPDATE_FN = "update_rnaseq_run_status"
# Statuses the poller still has to follow; later ones never change.
ACTIVE_STATUSES = ("submitted", "running")
# Recorded when the Workflow is gone before the poller saw it finish.
REMOVED_MESSAGE = "The workflow was removed before its result was recorded"
DEFAULT_POLL_SECONDS = 15.0


def _resolve_poll_interval() -> float:
    """WORKFLOWS_STATUS_POLL_SECONDS, or 15 s if unset or not a positive number."""
    try:
        value = float(
            os.environ.get("WORKFLOWS_STATUS_POLL_SECONDS", DEFAULT_POLL_SECONDS)
        )
    except ValueError:
        return DEFAULT_POLL_SECONDS
    return value if value > 0 else DEFAULT_POLL_SECONDS


POLL_INTERVAL = _resolve_poll_interval()

_running = True


def app_client():
    """Every call here is a small single-row read or RPC, so a tight timeout is safe."""
    return _app_client(timeout_seconds=SINGLE_ROW_RPC_TIMEOUT_SECONDS)


def _stop(signum, _frame):
    """Stops after the run in progress."""
    global _running
    logger.info("rnaseq_status_poller: received signal %s, stopping", signum)
    _running = False


def _active_runs(client) -> list[dict]:
    return (
        client.table(RUNS_TABLE)
        .select("id, workflow_type, params, run_key, status, argo_workflow_name")
        .in_("status", list(ACTIVE_STATUSES))
        .order("id")
        .execute()
        .data
        or []
    )


def _record(client, run_id, status: RunStatus) -> bool:
    return (
        client.rpc(
            UPDATE_FN,
            {
                "p_run_id": run_id,
                "p_status": status.status,
                "p_current_step": status.current_step,
                "p_step_pods": status.step_pods or None,
                "p_exit_code": status.exit_code,
                "p_message": status.message,
            },
        )
        .execute()
        .data
    )


def poll_run(client, run: dict) -> bool:
    """Reads one run's Workflow and records its status. Returns True if the run changed."""
    wf_type = WORKFLOW_TYPES.get(run["workflow_type"])
    if wf_type is None:
        logger.warning(
            "rnaseq_status_poller: run %s has unhandled workflow type %r",
            run["id"],
            run["workflow_type"],
        )
        return False
    name = run.get("argo_workflow_name")
    if not name:
        logger.warning("rnaseq_status_poller: run %s has no workflow name", run["id"])
        return False

    workflow = get_workflow(name)
    if workflow is None:
        status = RunStatus("failed", message=REMOVED_MESSAGE)
    else:
        status = wf_type.read_status(workflow, run)
        if status is None:
            return False

    changed = _record(client, run["id"], status)
    if changed:
        logger.info(
            "rnaseq_status_poller: run %s is %s at %s",
            run["id"],
            status.status,
            status.current_step,
        )
    return bool(changed)


def sweep_once(client) -> tuple[int, int]:
    """One pass over the active runs. Returns (runs checked, runs that errored).

    A K8s configuration error stops the pass, since every run would hit it."""
    runs = _active_runs(client)
    errors = 0
    for run in runs:
        if not _running:
            break
        try:
            poll_run(client, run)
        except K8sConfigError as exc:
            logger.error("rnaseq_status_poller: K8s not configured: %s", exc)
            return len(runs), len(runs)
        except Exception as exc:
            errors += 1
            logger.warning(
                "rnaseq_status_poller: run %s could not be updated: %s", run["id"], exc
            )
    return len(runs), errors


def _connect_with_retry():
    """Keeps retrying a Supabase connection at startup instead of crash-looping."""
    client = None
    while client is None and _running:
        try:
            client = app_client()
        except Exception as exc:
            logger.error(
                "rnaseq_status_poller: could not connect on startup, retrying in %ss: %s",
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
        logger.info("rnaseq status poller stopped before connecting")
        return
    logger.info(
        "rnaseq status poller started (poll=%ss, types=%s)",
        POLL_INTERVAL,
        ", ".join(WORKFLOW_TYPES),
    )
    while _running:
        try:
            sweep_once(client)
        except Exception as exc:
            logger.exception(
                "rnaseq_status_poller: sweep failed, reconnecting: %s", exc
            )
            try:
                client = app_client()
            except Exception as reconnect_exc:
                logger.error(
                    "rnaseq_status_poller: reconnect failed, will retry: %s",
                    reconnect_exc,
                )
        if _running:
            time.sleep(POLL_INTERVAL)
    logger.info("rnaseq status poller stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    run()
