"""
RNA-seq status poller.

Every WORKFLOWS_STATUS_POLL_SECONDS, reads the Argo Workflow of each submitted or running
rnaseq_runs row, turns it into the run's status with the reader for its workflow type in
rnaseq_workflows, and records it with update_rnaseq_run_status, which only moves a run
forward and writes nothing for an unchanged report. A Workflow the cluster says no longer
exists fails its run; any other failed read leaves the run for the next poll. A run that
imports its sample from SRA has the sample registered with register_rnaseq_sample once its
fetch-sra step succeeds. A run is linked to the dataset it loaded once its load-dataset
step succeeds. When a run finishes, its requester is emailed (run_email). Runs as the
bloom_workflows app user; one poller per environment.

Deploy: a container off the workflows image with `command: python rnaseq_status_poller.py`.
"""

import logging
import os
import signal
import time

from k8s_client import K8sConfigError, get_workflow
from postgrest import APIError

import run_email
from rnaseq_status import RunStatus, dataset_loaded, sra_download
from rnaseq_workflows import WORKFLOW_TYPES
from supabase_client import SINGLE_ROW_RPC_TIMEOUT_SECONDS
from supabase_client import app_client as _app_client

logger = logging.getLogger(__name__)

RUNS_TABLE = "rnaseq_runs"
UPDATE_FN = "update_rnaseq_run_status"
REGISTER_FN = "register_rnaseq_sample"
LINK_FN = "link_rnaseq_run_dataset"
# The function refused to link for now: the run isn't recorded as running yet.
LINK_NOT_YET = "55000"
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
# SRA runs whose sample this process has registered; the function is idempotent, so a
# restart that forgets them only repeats a harmless call.
_registered: set[int] = set()
# Runs already linked to their dataset, or whose link was refused for good.
_linked: set[int] = set()


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


def _register_sample(client, run: dict, workflow: dict, status: RunStatus) -> None:
    """Registers an SRA run's sample once its download has succeeded."""
    if run["id"] in _registered or not (run.get("params") or {}).get("sra_runs"):
        return
    if status.status not in ("running", "succeeded"):
        return
    download = sra_download(workflow)
    if download is None:
        return
    fastq_count, total_bytes = download
    try:
        client.rpc(
            REGISTER_FN,
            {
                "p_run_id": run["id"],
                "p_fastq_count": fastq_count,
                "p_total_bytes": total_bytes,
            },
        ).execute()
    except APIError as exc:
        # A conflicting name is a data problem, not a blip: say so once and stop trying.
        if exc.code == "23505":
            _registered.add(run["id"])
            logger.error(
                "rnaseq_status_poller: run %s's sample was not registered: %s",
                run["id"],
                exc.message,
            )
            return
        raise
    _registered.add(run["id"])
    logger.info(
        "rnaseq_status_poller: run %s registered sample %s (%s FASTQs, %s bytes)",
        run["id"],
        run["params"].get("sample"),
        fastq_count,
        total_bytes,
    )


def _link_dataset(client, run: dict, workflow: dict) -> None:
    """Links a run to the dataset its load-dataset step loaded, gives the dataset the form's
    details and hands it to the scientist. Called before the run's status is recorded, so a
    failed call leaves the run active and it is tried again at the next poll, and once more
    after a final status is recorded, for a run that finished before it was seen running."""
    if run["id"] in _linked or not dataset_loaded(workflow):
        return
    try:
        dataset_id = client.rpc(LINK_FN, {"p_run_id": run["id"]}).execute().data
    except APIError as exc:
        if exc.code == LINK_NOT_YET:
            logger.info(
                "rnaseq_status_poller: run %s's dataset isn't linked yet: %s",
                run["id"],
                exc.message,
            )
            return
        # Not one this run can fix by waiting (no such dataset, another run's): say so once.
        _linked.add(run["id"])
        logger.error(
            "rnaseq_status_poller: run %s was not linked to its dataset: %s",
            run["id"],
            exc.message,
        )
        return
    _linked.add(run["id"])
    logger.info(
        "rnaseq_status_poller: run %s is linked to dataset %s", run["id"], dataset_id
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

    if workflow is not None:
        _link_dataset(client, run, workflow)
    changed = _record(client, run["id"], status)
    if workflow is not None and status.status not in ACTIVE_STATUSES:
        _link_dataset(client, run, workflow)
    if workflow is not None:
        _register_sample(client, run, workflow, status)
    # notify() skips a run still going, and a finished run changes only once, so one email.
    if changed:
        run_email.notify(client, run, status)
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
