"""
What an RNA-seq run's Argo Workflow says about the run: its status, current step, step
pods and, once it ends, its exit code and message.

`read_cellranger_status` reads a Cell Ranger Workflow as built by rnaseq_workflows.
Argo lists every step and every attempt in `status.nodes`; a step's pod name is the
Workflow name, the step's template and the numeric end of its node id, joined by '-'.
"""

from dataclasses import dataclass, field

# Output folder for a run's results; the pipeline uploads there.
RESULTS_PREFIX = "s3://bloomv2-workflows/runs_output"

# Cell Ranger's pipeline steps by template name, in the order they run.
CELLRANGER_STEPS = {
    "stage-reference": "stage-reference",
    "stage-sample": "stage",
    "qc": "qc",
    "count": "count",
    "cleanup": "cleanup",
}
_STEP_ORDER = {step: i for i, step in enumerate(CELLRANGER_STEPS.values())}

# Exit codes set by the pipeline scripts in argo/scrna/cellranger/.
EXIT_NO_REFERENCE = 3
EXIT_NO_FASTQS = 4
EXIT_CELLRANGER_FAILED = 5
EXIT_BAD_SAMPLE_NAME = 6

_RUNNING_PHASES = {"Pending", "Running"}
_FAILED_PHASES = {"Failed", "Error"}


@dataclass(frozen=True)
class RunStatus:
    status: str
    current_step: str | None = None
    step_pods: dict[str, str] = field(default_factory=dict)
    exit_code: int | None = None
    message: str | None = None


def _template(node: dict) -> str | None:
    return (node.get("templateRef") or {}).get("template") or node.get("templateName")


def _pod_name(workflow_name: str, node: dict) -> str:
    return f"{workflow_name}-{_template(node)}-{node['id'].rsplit('-', 1)[-1]}"


def _exit_code(node: dict) -> int | None:
    raw = (node.get("outputs") or {}).get("exitCode")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _output(node: dict, name: str) -> str | None:
    for param in (node.get("outputs") or {}).get("parameters") or []:
        if param.get("name") == name:
            return param.get("value")
    return None


def _step_pods(workflow: dict) -> dict[str, dict]:
    """Each Cell Ranger step's latest Pod node, keyed by step."""
    latest: dict[str, dict] = {}
    for node in (workflow.get("status") or {}).get("nodes", {}).values():
        step = CELLRANGER_STEPS.get(_template(node))
        if node.get("type") != "Pod" or step is None or not node.get("startedAt"):
            continue
        if step not in latest or node["startedAt"] >= latest[step]["startedAt"]:
            latest[step] = node
    return latest


def _failure_message(step: str, exit_code: int | None, run: dict) -> str:
    params = run.get("params") or {}
    if exit_code == EXIT_NO_REFERENCE:
        return f"No reference at reference_genome/{params.get('reference')}/"
    if exit_code == EXIT_NO_FASTQS:
        return f"No FASTQs at raw_reads/{params.get('sample')}/"
    if exit_code == EXIT_CELLRANGER_FAILED:
        return (
            "Cell Ranger failed; its log is at "
            f"runs_output/{run.get('run_key')}/logs/count.log"
        )
    if exit_code == EXIT_BAD_SAMPLE_NAME:
        return (
            f"Sample {params.get('sample')} can't be used as a Cell Ranger run id "
            "(letters, digits, '_' or '-', at most 64)"
        )
    if exit_code is None:
        return f"Step {step} failed"
    return f"Step {step} failed (exit {exit_code})"


def read_cellranger_status(workflow: dict, run: dict) -> RunStatus | None:
    """The run's status from its Workflow, or None while no step has started."""
    name = workflow["metadata"]["name"]
    phase = (workflow.get("status") or {}).get("phase")
    pods = _step_pods(workflow)
    step_pods = {step: _pod_name(name, node) for step, node in pods.items()}

    running = [s for s, n in pods.items() if n.get("phase") in _RUNNING_PHASES]
    started = sorted(pods, key=_STEP_ORDER.__getitem__)
    current = (running or started or [None])[-1]
    results = f"{RESULTS_PREFIX}/{run.get('run_key')}/"

    if phase in _FAILED_PHASES:
        failed = [s for s, n in pods.items() if n.get("phase") in _FAILED_PHASES]
        if not failed:
            return RunStatus(
                "failed",
                current,
                step_pods,
                None,
                "The workflow failed before a step ran",
            )
        step = max(failed, key=lambda s: pods[s].get("finishedAt") or "")
        code = _exit_code(pods[step])
        return RunStatus(
            "failed", step, step_pods, code, _failure_message(step, code, run)
        )

    if phase == "Succeeded":
        if _output(pods.get("stage", {}), "done") == "true":
            return RunStatus(
                "skipped", current, step_pods, 0, f"Already done: results in {results}"
            )
        return RunStatus(
            "succeeded", current, step_pods, 0, f"Finished: results in {results}"
        )

    if current is None:
        return None
    return RunStatus("running", current, step_pods)
