"""
Pipeline status poller (bloom #11 Phase 3).

Periodically re-checks every `cyl_pipeline_runs` row still `'submitted'`/
`'running'`, fetches the real Argo Workflow phase for each of that run's
distinct `argo_workflow_name`s via k8s_client.get_workflow_status, computes
the run's rollup status (see the rollup rule below), and writes it via the
`update_cyl_pipeline_run_status` SECURITY DEFINER RPC. Distinct from
`dispatch_worker.py`: that worker reacts to new pgmq messages (event-driven);
this poller runs on a fixed wall-clock cadence regardless of dispatch
activity, sweeping every currently-active run. Runs as the least-privilege
bloom_workflows app user — no direct DB connection.

Deploy: a container off the workflows image with `command: python
status_poller.py`.

Env:
    WORKFLOWS_STATUS_POLL_SECONDS  sleep between sweep cycles (default 15)
"""

import logging
import os
import signal
import time

from k8s_client import K8sConfigError, K8sStatusError, get_workflow_status
from supabase_client import SINGLE_ROW_RPC_TIMEOUT_SECONDS
from supabase_client import app_client as _app_client


def app_client():
    """This poller's RPC/reads are all small, single-row/single-run,
    indexed operations — safe to bound tighter than supabase-py's 120s
    default (see supabase_client.py's SINGLE_ROW_RPC_TIMEOUT_SECONDS)."""
    return _app_client(timeout_seconds=SINGLE_ROW_RPC_TIMEOUT_SECONDS)


logger = logging.getLogger(__name__)


def _resolve_poll_interval() -> float:
    """Never raises — a present-but-malformed value must degrade to the safe
    default, not crash the module at import time, matching k8s_client.py's
    _resolve_ttl_seconds() convention for the same class of failure mode."""
    raw = os.environ.get("WORKFLOWS_STATUS_POLL_SECONDS", "15")
    try:
        return float(raw)
    except ValueError:
        return 15.0


POLL_INTERVAL = _resolve_poll_interval()

_running = True


def _stop(signum, _frame):
    """SIGTERM/SIGINT -> flip the running-flag only. Does not interrupt an
    in-flight sweep_once() call, so a run's in-flight check/update always
    finishes; the loop simply doesn't start a new sweep cycle after."""
    global _running
    logger.info(
        "status_poller: received signal %s, shutting down after current sweep",
        signum,
    )
    _running = False


def rollup(effective_phases: list[str]) -> str | None:
    """Pure rollup rule (cyl-pipeline-status-polling spec's "Rollup rule..."
    requirement): rule (0) an empty list concludes nothing (None) — checked
    first so it can never vacuously satisfy rule (2)'s all()-over-Succeeded.
    Otherwise: (1) any Pending/Running -> 'running'; (2) all Succeeded ->
    'complete'; (3) none Succeeded -> 'failed'; (4) a mix -> 'partial'."""
    if not effective_phases:
        return None
    if any(p in ("Pending", "Running") for p in effective_phases):
        return "running"
    if all(p == "Succeeded" for p in effective_phases):
        return "complete"
    if not any(p == "Succeeded" for p in effective_phases):
        return "failed"
    return "partial"


def _fetch_candidate_runs(client) -> list[dict]:
    """Every run still eligible for real-outcome polling — dispatch already
    fully or partially succeeded ('submitted'), or a prior sweep already
    started progressing it ('running'). A 'queued' run was never dispatched;
    anything already 'complete'/'failed'/'partial' is terminal."""
    return (
        client.table("cyl_pipeline_runs")
        .select("id")
        .in_("status", ["submitted", "running"])
        .execute()
        .data
        or []
    )


def _fetch_effective_phases(client, run_id) -> list[str]:
    """One run's effective-phase list: 'Failed' for each scan whose dispatch
    itself failed (status='failed', argo_workflow_name IS NULL), plus the
    real Argo phase of each distinct argo_workflow_name among the run's
    scans (a 404/None result is excluded, not guessed — see
    get_workflow_status). A K8sConfigError/K8sStatusError from
    get_workflow_status propagates to the caller, which is responsible for
    leaving this run unsettled and moving on to the next candidate."""
    rows = (
        client.table("cyl_pipeline_run_scans")
        .select("argo_workflow_name, status")
        .eq("run_id", run_id)
        .execute()
        .data
        or []
    )

    phases = []
    if any(
        r.get("argo_workflow_name") is None and r.get("status") == "failed"
        for r in rows
    ):
        phases.append("Failed")

    workflow_names = sorted(
        {r["argo_workflow_name"] for r in rows if r.get("argo_workflow_name")}
    )
    for name in workflow_names:
        phase = get_workflow_status(name)
        if phase is not None:
            phases.append(phase)

    return phases


def update_run_status(client, run_id, status: str) -> None:
    client.rpc(
        "update_cyl_pipeline_run_status", {"p_run_id": run_id, "p_status": status}
    ).execute()


def sweep_once(client) -> None:
    """One full cycle: check every candidate run, updating those with a
    concluded rollup status. A failure checking or updating one run (a
    transient K8s API blip, a K8sConfigError, a lost RPC) is isolated to that
    run — it must not abort the cycle for the rest of the candidates."""
    for run in _fetch_candidate_runs(client):
        run_id = run["id"]
        try:
            phases = _fetch_effective_phases(client, run_id)
        except (K8sConfigError, K8sStatusError) as exc:
            logger.warning(
                "status_poller: run %s status check failed, leaving unsettled "
                "for the next cycle: %s",
                run_id,
                exc,
            )
            continue

        status = rollup(phases)
        if status is None:
            continue

        try:
            update_run_status(client, run_id, status)
        except Exception as exc:
            logger.error(
                "status_poller: run %s update to %s failed, will retry next "
                "cycle: %s",
                run_id,
                status,
                exc,
            )
            continue

        logger.info("status_poller: run %s -> %s", run_id, status)


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
                "status_poller: could not connect on startup, retrying in %ss: %s",
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
        logger.info("status poller stopped before connecting")
        return
    logger.info("status poller started (poll=%ss)", POLL_INTERVAL)
    while _running:
        try:
            sweep_once(client)
        except Exception as exc:
            # A loop-level error (e.g. an expired session) — reconnect and retry.
            logger.exception("status_poller: sweep error, reconnecting: %s", exc)
            time.sleep(POLL_INTERVAL)
            try:
                client = app_client()
            except Exception as reconnect_exc:
                logger.error(
                    "status_poller: reconnect failed, will retry: %s",
                    reconnect_exc,
                )
            continue
        if _running:
            time.sleep(POLL_INTERVAL)
    logger.info("status poller stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    run()
