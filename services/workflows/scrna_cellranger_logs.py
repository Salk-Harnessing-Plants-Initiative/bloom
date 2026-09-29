"""
Reading one step's log of a Cell Ranger run.

The status poller records each started step's pod in the run's `step_pods`; this reads
the last part of that pod's `main` container log from the Kubernetes API, as the
workflows service's `bloom-pipeline` account. A pod is removed with its Workflow, 24
hours after the run finishes, so older logs are gone.
"""

import re

from fastapi import HTTPException

from k8s_client import (
    K8sConfigError,
    K8sPodNotRunningError,
    K8sStatusError,
    get_pod_log,
)
from rnaseq_status import CELLRANGER_STEPS
from supabase_client import app_client

RUNS_TABLE = "rnaseq_runs"
WORKFLOW_TYPE = "scrna-cellranger"
STEPS = tuple(CELLRANGER_STEPS.values())

# Argo runs each step's own command in the pod's "main" container.
STEP_CONTAINER = "main"
# How much of a log one request returns: its last lines, capped in size.
TAIL_LINES = 2000
LIMIT_BYTES = 1024 * 1024
# Asked of the cluster; it cuts at this size from the start of the window, so it is
# larger than LIMIT_BYTES and the end is trimmed here instead.
FETCH_LIMIT_BYTES = 4 * LIMIT_BYTES

# A Kubernetes pod name: lowercase letters, digits and '-', at most 253 characters.
_POD_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]{0,251}[a-z0-9])?$")


def _keep_end(log: str) -> tuple[str, bool]:
    """The log's last LIMIT_BYTES, starting at a line, and whether anything was cut."""
    data = log.encode()
    if len(data) <= LIMIT_BYTES:
        return log, False
    end = data[-LIMIT_BYTES:].decode(errors="ignore")
    newline = end.find("\n")
    return (end[newline + 1 :] if newline != -1 else end), True


def read_step_log(run_id: int, step) -> dict:
    """The end of one step's log as {run_id, step, pod, log, truncated}."""
    if step not in STEPS:
        raise HTTPException(
            status_code=422, detail=f"step must be one of: {', '.join(STEPS)}"
        )

    rows = (
        app_client()
        .table(RUNS_TABLE)
        .select("id, step_pods")
        .eq("id", run_id)
        .eq("workflow_type", WORKFLOW_TYPE)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"Cell Ranger run {run_id} not found"
        )

    pod = (rows[0].get("step_pods") or {}).get(step)
    if not pod:
        raise HTTPException(
            status_code=404, detail=f"Step {step} of run {run_id} has not started"
        )
    if not isinstance(pod, str) or not _POD_NAME.match(pod):
        raise HTTPException(
            status_code=500, detail=f"Run {run_id} has an invalid pod for step {step}"
        )

    try:
        log = get_pod_log(pod, STEP_CONTAINER, TAIL_LINES, FETCH_LIMIT_BYTES)
    except K8sPodNotRunningError:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Step {step} of run {run_id} hasn't started running yet; "
                "its log appears once it does"
            ),
        ) from None
    except K8sConfigError:
        raise HTTPException(
            status_code=503, detail="Cluster access is not configured"
        ) from None
    except K8sStatusError:
        raise HTTPException(
            status_code=502, detail="Could not read the log from the cluster"
        ) from None

    if log is None:
        raise HTTPException(
            status_code=410,
            detail=(
                f"The log of step {step} is no longer available; the workflow "
                "was removed from the cluster"
            ),
        )

    log, cut = _keep_end(log)
    truncated = cut or log.count("\n") >= TAIL_LINES
    return {
        "run_id": run_id,
        "step": step,
        "pod": pod,
        "log": log,
        "truncated": truncated,
    }
