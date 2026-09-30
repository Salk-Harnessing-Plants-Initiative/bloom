"""
What an RNA-seq run's Argo Workflow says about the run: its status, current step, step
pods and, once it ends, its exit code and message.

`read_cellranger_status` reads a Cell Ranger Workflow as built by rnaseq_workflows.
Argo lists every step and every attempt in `status.nodes`; a step's pod name is the
Workflow name, the step's template and the numeric end of its node id, joined by '-'.
"""

from dataclasses import dataclass, field

# Output folder for a run's results; the pipeline uploads the final .h5ad to <run_key>/h5ad/.
RESULTS_PREFIX = "s3://bloomv2-workflows/runs_output"
# The cluster's shared folder, where a run's files stay after a failure.
SHARED_RUNS = "/hpi/hpi_dev/users/bfernando/scrna/runs"

# Cell Ranger's pipeline steps by template name, in the order they run; fetch-sra runs only
# for a run that imports its sample from SRA.
CELLRANGER_STEPS = {
    "fetch-sra": "fetch-sra",
    "stage-reference": "stage-reference",
    "stage-sample": "stage",
    "qc": "qc",
    "count": "count",
    "preprocess": "preprocess",
    "cluster": "cluster",
    "build-h5ad": "build-h5ad",
    "cleanup": "cleanup",
}
_STEP_ORDER = {step: i for i, step in enumerate(CELLRANGER_STEPS.values())}

# Exit codes set by the pipeline scripts in argo/scrna/cellranger/.
EXIT_NO_REFERENCE = 3
EXIT_NO_FASTQS = 4
EXIT_CELLRANGER_FAILED = 5
EXIT_BAD_SAMPLE_NAME = 6
EXIT_BAD_FASTQ_NAMES = 7
# fetch-sra (argo/scrna/cellranger/fetch-sra.sh) also uses 6 and 7, for its own input.
EXIT_SRA_TRANSFER_FAILED = 10
EXIT_SRA_READS_UNUSABLE = 11
EXIT_SRA_FOLDER_TAKEN = 12
# The analysis steps (argo/scrna/analysis/bloom_scrna_analysis/steps.py).
EXIT_TOO_FEW_CELLS = 13
EXIT_NO_MATRIX = 14
EXIT_PARTS_DONT_FIT = 15

_ANALYSIS_MESSAGES = {
    EXIT_TOO_FEW_CELLS: "Fewer than 50 cells passed the filters, too few to cluster",
    EXIT_NO_MATRIX: "Cell Ranger's count matrix wasn't found for the analysis steps",
    EXIT_PARTS_DONT_FIT: "An analysis step's results didn't fit the others; the {step} step's log has the details",
}

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


def _fetch_sra_message(exit_code: int | None, params: dict) -> str | None:
    sample = params.get("sample")
    runs = ", ".join(params.get("sra_runs") or [])
    return {
        EXIT_BAD_SAMPLE_NAME: f"Sample {sample} or SRA run IDs {runs} can't be used",
        EXIT_BAD_FASTQ_NAMES: f"The FASTQs downloaded for {sample} couldn't be named the Illumina way",
        EXIT_SRA_TRANSFER_FAILED: (
            f"Couldn't download {runs} from SRA, or couldn't check raw_reads/{sample}/ in "
            "storage; start the run again, and check the run IDs are public if it fails again"
        ),
        EXIT_SRA_READS_UNUSABLE: (
            f"An SRA run in {runs} lacks the 10x barcode or cDNA read; it may have been "
            "submitted as a BAM"
        ),
        EXIT_SRA_FOLDER_TAKEN: (
            f"raw_reads/{sample}/ already holds other FASTQs; choose another sample name"
        ),
    }.get(exit_code)


def _failure_message(step: str, exit_code: int | None, run: dict) -> str:
    params = run.get("params") or {}
    if step == "fetch-sra":
        message = _fetch_sra_message(exit_code, params)
        if message:
            return message
    if exit_code in _ANALYSIS_MESSAGES:
        return _ANALYSIS_MESSAGES[exit_code].format(step=step)
    if exit_code == EXIT_NO_REFERENCE:
        return f"No reference at reference_genome/{params.get('reference')}/"
    if exit_code == EXIT_NO_FASTQS:
        return f"No FASTQs at raw_reads/{params.get('sample')}/"
    if exit_code == EXIT_CELLRANGER_FAILED:
        return (
            "Cell Ranger failed; its log is at "
            f"{SHARED_RUNS}/{run.get('run_key')}/logs/count.log"
        )
    if exit_code == EXIT_BAD_SAMPLE_NAME:
        return (
            f"Sample {params.get('sample')} can't be used as a Cell Ranger run id "
            "(letters, digits, '_' or '-', at most 64)"
        )
    if exit_code == EXIT_BAD_FASTQ_NAMES:
        return (
            f"The FASTQs in raw_reads/{params.get('sample')}/ must be named like "
            "<name>_S1_L001_R1_001.fastq.gz, with an R1 and an R2 for every lane; "
            f"the {step} step's log lists the files"
        )
    if exit_code is None:
        return f"Step {step} failed"
    return f"Step {step} failed (exit {exit_code})"


def sra_download(workflow: dict) -> tuple[int, int] | None:
    """The FASTQ count and total bytes fetch-sra reported, once it has succeeded."""
    node = _step_pods(workflow).get("fetch-sra")
    if not node or node.get("phase") != "Succeeded":
        return None
    try:
        return int(_output(node, "fastq-count")), int(_output(node, "total-bytes"))
    except (TypeError, ValueError):
        return None


def read_cellranger_status(workflow: dict, run: dict) -> RunStatus | None:
    """The run's status from its Workflow, or None while no step has started."""
    name = workflow["metadata"]["name"]
    phase = (workflow.get("status") or {}).get("phase")
    pods = _step_pods(workflow)
    step_pods = {step: _pod_name(name, node) for step, node in pods.items()}

    running = [s for s, n in pods.items() if n.get("phase") in _RUNNING_PHASES]
    started = sorted(pods, key=_STEP_ORDER.__getitem__)
    current = (running or started or [None])[-1]
    results = f"{RESULTS_PREFIX}/{run.get('run_key')}/h5ad/"

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
