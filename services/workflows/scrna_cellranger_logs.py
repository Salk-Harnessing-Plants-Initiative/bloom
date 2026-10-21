"""
Reading one step's log of a Cell Ranger run.

The status poller records each started step's pod in the run's `step_pods`; every step
saves its log to the run-logs bucket as it runs (argo/scrna/run_with_log.py), at
scrna/<workflow>/<template>.log. This reads the last part of that file, as the workflows
service's login, so it needs no cluster access and outlives the Workflow.
"""

import re

from fastapi import HTTPException

from rnaseq_status import CELLRANGER_STEPS, PHASES_KEY, RETIRED_CELLRANGER_STEPS
from supabase_client import app_client

RUNS_TABLE = "rnaseq_runs"
WORKFLOW_TYPE = "scrna-cellranger"
# Earlier runs' retired steps too, so their logs can still be read.
STEPS = tuple(dict.fromkeys(CELLRANGER_STEPS.values())) + RETIRED_CELLRANGER_STEPS

# Where run-with-log saves each step's log.
LOGS_BUCKET = "run-logs"
LOG_PREFIX = "scrna"
# Templates whose log file isn't named after them (their run-with-log --path).
LOG_NAMES = {"stage-sample": "stage"}
# Templates without run-with-log: the pipeline's upload-logs saves their logs once they end.
SAVED_AT_END = frozenset({"proteins", "eggnog-db", "emapper"})
# A run that has ended, and a step's Argo phases once it has ended (rnaseq_status' _phases).
_RUN_ENDED = frozenset({"succeeded", "failed", "skipped"})
_STEP_ENDED = frozenset({"Succeeded", "Failed", "Error", "Omitted", "Skipped"})
# How much of a log one request returns: its last lines, capped in size.
TAIL_LINES = 2000
LIMIT_BYTES = 1024 * 1024

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


def _template(pod: str, workflow: str) -> str | None:
    """The step's template from its pod name, <workflow>-<template>-<node id>."""
    if not workflow or not pod.startswith(f"{workflow}-"):
        return None
    template = pod[len(workflow) + 1 :].rsplit("-", 1)[0]
    return template if template and _POD_NAME.match(template) else None


def _missing(exc: Exception) -> bool:
    """Whether storage answered that the file isn't there (storage3's StorageApiError)."""
    return str(getattr(exc, "status", "")) == "404" or getattr(exc, "code", "") == "not_found"


def _no_log(run_id: int, step: str, row: dict, template: str) -> str:
    """Why a started step has no log in the bucket, for the run page."""
    phase = ((row.get("step_pods") or {}).get(PHASES_KEY) or {}).get(step)
    if row.get("status") in _RUN_ENDED or phase in _STEP_ENDED:
        return f"Step {step} of run {run_id} has no saved log"
    if template in SAVED_AT_END:
        return f"Step {step} of run {run_id} saves its log when it ends"
    return (
        f"Step {step} of run {run_id} has no log in Bloom yet; a running step saves its "
        "log every 30 seconds"
    )


def read_step_log(run_id: int, step) -> dict:
    """The end of one step's log as {run_id, step, pod, log, truncated}."""
    if step not in STEPS:
        raise HTTPException(
            status_code=422, detail=f"step must be one of: {', '.join(STEPS)}"
        )

    rows = (
        app_client()
        .table(RUNS_TABLE)
        .select("id, status, step_pods, argo_workflow_name")
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

    template = _template(pod, rows[0].get("argo_workflow_name") or "")
    if template is None:
        raise HTTPException(
            status_code=500, detail=f"Run {run_id} has an invalid pod for step {step}"
        )
    path = f"{LOG_PREFIX}/{rows[0]['argo_workflow_name']}/{LOG_NAMES.get(template, template)}.log"
    try:
        data = app_client().storage.from_(LOGS_BUCKET).download(path)
    except Exception as exc:  # noqa: BLE001 - storage3 and httpx raise several kinds
        if _missing(exc):
            raise HTTPException(
                status_code=404, detail=_no_log(run_id, step, rows[0], template)
            ) from None
        raise HTTPException(
            status_code=502, detail="Could not read the log from Bloom's storage"
        ) from None

    lines = data.decode(errors="replace").splitlines(keepends=True)
    log, cut = _keep_end("".join(lines[-TAIL_LINES:]))
    truncated = cut or len(lines) > TAIL_LINES
    return {
        "run_id": run_id,
        "step": step,
        "pod": pod,
        "log": log,
        "truncated": truncated,
    }
