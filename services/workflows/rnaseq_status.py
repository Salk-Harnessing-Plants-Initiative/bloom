"""
What an RNA-seq run's Argo Workflow says about the run: its status, current step, step
pods and, once it ends, its exit code and message.

`read_cellranger_status` reads a Cell Ranger Workflow as built by rnaseq_workflows.
Argo lists every step and every attempt in `status.nodes`; a step's pod name is the
Workflow name, the step's template and the numeric end of its node id, joined by '-'.
"""

from dataclasses import dataclass, field

# The cluster's shared folder, where a run's files stay after a failure.
SHARED_RUNS = "/hpi/hpi_dev/users/bfernando/scrna/runs"

# Cell Ranger's pipeline steps by template name, in the order the run page shows them
# (web/lib/rnaseq-runs.ts; a test keeps the two the same); fetch-sra runs only for a run that
# imports its sample from SRA. The genome steps run alongside the reads' steps and usually
# finish last, so they come just before count.
CELLRANGER_STEPS = {
    "fetch-sra": "fetch-sra",
    "stage-sample": "stage",
    "qc": "qc",
    "download-genome": "download-genome",
    "build-reference": "build-reference",
    # The genome's GO and pathway annotation (go-pathways-template), alongside the reads and count;
    # one run-page step for all its pods.
    "find-annotation": "annotate-genome",
    "fetch-references": "annotate-genome",
    "proteins": "annotate-genome",
    "eggnog-db": "annotate-genome",
    "emapper": "annotate-genome",
    "build-annotation": "annotate-genome",
    "upload-annotation": "annotate-genome",
    "count": "count",
    "preprocess": "preprocess",
    "cluster": "cluster",
    "de": "de",
    "build-h5ad": "build-h5ad",
    "load-dataset": "load-dataset",
    # The new dataset's enrichment against that annotation.
    "fetch-de": "go-pathways",
    "enrich": "go-pathways",
    "upload-enrichment": "go-pathways",
    "cleanup": "cleanup",
}
_STEP_ORDER = {step: i for i, step in enumerate(dict.fromkeys(CELLRANGER_STEPS.values()))}
# Steps that may fail without failing the run: the dataset is loaded either way.
GO_STEPS = ("annotate-genome", "go-pathways")
# The key in step_pods that holds each step's own outcome (Argo's phase), for the run page;
# no step is named it.
PHASES_KEY = "_phases"
# Steps the template no longer has, which runs started before still list.
RETIRED_CELLRANGER_STEPS = ("stage-reference",)

# Exit codes set by the pipeline scripts in argo/scrna/cellranger/.
EXIT_NO_REFERENCE = 3
EXIT_NO_FASTQS = 4
EXIT_CELLRANGER_FAILED = 5
EXIT_BAD_SAMPLE_NAME = 6
EXIT_BAD_FASTQ_NAMES = 7
# stage-fastqs: the run's S3 folder changed after the run was started.
EXIT_FOLDER_CHANGED = 8
# stage-fastqs: the run's FASTQs are named for another sample.
EXIT_OTHER_SAMPLE = 9
# fetch-sra (argo/scrna/cellranger/fetch-sra.sh) also uses 6 and 7, for its own input.
# fetch-sra and stage-fastqs: a download or copy failed.
EXIT_TRANSFER_FAILED = 10
EXIT_SRA_READS_UNUSABLE = 11
# The analysis steps (argo/scrna/analysis/bloom_scrna_analysis/steps.py).
EXIT_TOO_FEW_CELLS = 13
EXIT_NO_MATRIX = 14
EXIT_PARTS_DONT_FIT = 15
# load-dataset and download-genome (argo/scrna/analysis/): the pipeline has no Bloom credentials.
EXIT_NO_CREDENTIALS = 16
# build-reference: cellranger mkref failed on the downloaded genome.
EXIT_REFERENCE_BUILD_FAILED = 17
# download-genome: bloomctl couldn't reach Bloom or its storage, after the step's retries.
EXIT_DOWNLOAD_FAILED = 1

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
        EXIT_TRANSFER_FAILED: (
            f"Couldn't download {runs} from ENA or SRA; start the run again, and check the "
            "run IDs are public if it fails again"
        ),
        EXIT_SRA_READS_UNUSABLE: (
            f"The reads of an SRA run in {runs} don't look like 10x gene-expression reads; "
            "the fetch-sra log says which run and why"
        ),
    }.get(exit_code)


def _folder_message(exit_code: int | None, params: dict) -> str | None:
    url = params.get("fastq_url")
    return {
        EXIT_NO_FASTQS: (
            f"The FASTQs in {url} were removed after the run was started; start a new run "
            "once they're back"
        ),
        EXIT_BAD_SAMPLE_NAME: (
            f"The run's folder {url} or its recorded file list couldn't be used; this isn't "
            "something wrong with the folder, so ask the Bloom admins (the stage step's log "
            "has the details)"
        ),
        EXIT_BAD_FASTQ_NAMES: (
            f"The FASTQs in {url} must be named like <name>_S1_L001_R1_001.fastq.gz, with an "
            "R1 and an R2 for every lane; the stage step's log lists the files"
        ),
        EXIT_FOLDER_CHANGED: (
            f"{url} changed after the run was started, so its reads weren't used; the stage "
            "step's log says which file. Start a new run on the folder as it is now"
        ),
        EXIT_OTHER_SAMPLE: (
            f"The FASTQs in {url} are named for another sample than the run's, "
            f"{params.get('sample')}; the stage step's log names it"
        ),
        EXIT_TRANSFER_FAILED: (
            f"Couldn't list or copy {url}; the stage step's log has S3's error. Check the "
            "folder is still public and its files aren't archived, then start the run again"
        ),
    }.get(exit_code)


def _fastqs(params: dict) -> str:
    """The run's FASTQs, by where they came from, for the messages any step can give."""
    if params.get("fastq_url"):
        return f"The FASTQs copied from {params['fastq_url']}"
    return f"The FASTQs downloaded for {', '.join(params.get('sra_runs') or [])}"


def _genome_message(step: str, exit_code: int | None, params: dict) -> str | None:
    genome = params.get("reference")
    if step == "download-genome":
        return {
            EXIT_BAD_SAMPLE_NAME: (
                f"The run's genome {genome} or its folder couldn't be used; this isn't something "
                "wrong with the genome, so ask the Bloom admins"
            ),
            EXIT_NO_REFERENCE: (
                f"Genome {genome} isn't available in Bloom, or a downloaded file doesn't match "
                "Bloom's record; the download-genome step's log says which"
            ),
            EXIT_DOWNLOAD_FAILED: (
                f"Couldn't download genome {genome} from Bloom; start the run again, and ask the "
                "Bloom admins if it fails again"
            ),
            EXIT_NO_CREDENTIALS: (
                "The pipeline has no Bloom credentials, so the genome couldn't be downloaded; "
                "ask the Bloom admins"
            ),
        }.get(exit_code)
    if step == "build-reference":
        if exit_code == EXIT_BAD_SAMPLE_NAME:
            return (
                f"The run's genome {genome} or its folder couldn't be used; this isn't something "
                "wrong with the genome, so ask the Bloom admins"
            )
        if exit_code == EXIT_REFERENCE_BUILD_FAILED:
            return (
                f"Cell Ranger couldn't build a reference from genome {genome}; the "
                "build-reference step's log has Cell Ranger's error"
            )
        if exit_code == EXIT_NO_REFERENCE:
            return (
                f"The files of genome {genome} weren't found on the shared disk to build from; "
                "start the run again, and ask the Bloom admins if it fails again"
            )
    return None


def _load_message(exit_code: int | None) -> str:
    if exit_code == EXIT_NO_CREDENTIALS:
        return (
            "The pipeline has no Bloom credentials, so the dataset wasn't loaded; ask the "
            "Bloom admins"
        )
    return "Loading the dataset into Bloom failed; the load-dataset step's log says why"


def _failure_message(step: str, exit_code: int | None, run: dict) -> str:
    params = run.get("params") or {}
    if step == "load-dataset":
        return _load_message(exit_code)
    if step in ("download-genome", "build-reference"):
        message = _genome_message(step, exit_code, params)
        if message:
            return message
        return _step_failed(step, exit_code)
    if step == "stage" and params.get("fastq_url"):
        message = _folder_message(exit_code, params)
        if message:
            return message
    if step == "fetch-sra":
        message = _fetch_sra_message(exit_code, params)
        if message:
            return message
    if exit_code in _ANALYSIS_MESSAGES:
        return _ANALYSIS_MESSAGES[exit_code].format(step=step)
    if exit_code == EXIT_NO_REFERENCE:
        return f"No Cell Ranger reference for {params.get('reference')} was found to count against"
    if exit_code == EXIT_NO_FASTQS:
        if params.get("fastq_url"):
            return f"No FASTQs were found on the shared disk after copying {params['fastq_url']}"
        return f"No FASTQs were found on the shared disk for {params.get('sample')}"
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
            f"{_fastqs(params)} must be named like "
            "<name>_S1_L001_R1_001.fastq.gz, with an R1 and an R2 for every lane; "
            f"the {step} step's log lists the files"
        )
    return _step_failed(step, exit_code)


def _step_failed(step: str, exit_code: int | None) -> str:
    if exit_code is None:
        return f"Step {step} failed"
    return f"Step {step} failed (exit {exit_code})"


def dataset_loaded(workflow: dict) -> bool:
    """Whether the run named a dataset and its load-dataset step has succeeded, so the
    dataset is in Bloom. A run naming none succeeds the step without loading anything."""
    parameters = ((workflow.get("spec") or {}).get("arguments") or {}).get(
        "parameters"
    ) or []
    named = any(
        p.get("name") == "dataset-name" and str(p.get("value") or "").strip()
        for p in parameters
    )
    node = _step_pods(workflow).get("load-dataset")
    return named and bool(node) and node.get("phase") == "Succeeded"


def loaded_dataset(workflow: dict) -> int | None:
    """The id of the dataset the load-dataset step loaded and reported, once it has; None
    otherwise, including for a run started by hand, which reports none."""
    if not dataset_loaded(workflow):
        return None
    try:
        return int(_output(_step_pods(workflow)["load-dataset"], "dataset-id") or "")
    except ValueError:
        return None


def read_cellranger_status(workflow: dict, run: dict) -> RunStatus | None:
    """The run's status from its Workflow, or None while no step has started."""
    name = workflow["metadata"]["name"]
    phase = (workflow.get("status") or {}).get("phase")
    pods = _step_pods(workflow)
    step_pods = {step: _pod_name(name, node) for step, node in pods.items()}
    phases = step_phases(workflow)

    # By step order, so steps running side by side always give the same current step.
    started = sorted(pods, key=_STEP_ORDER.__getitem__)
    running = [s for s in started if pods[s].get("phase") in _RUNNING_PHASES]
    current = (running or started or [None])[-1]

    if phase in _FAILED_PHASES:
        failed = [s for s, n in pods.items() if n.get("phase") in _FAILED_PHASES]
        # A GO step's failure doesn't fail the run; another step's did.
        failed = [s for s in failed if s not in GO_STEPS] or failed
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
        if dataset_loaded(workflow):
            message = "Finished: loaded into Bloom" + _go_note(pods, phases)
        else:
            message = "Finished; the run named no dataset, so nothing was loaded into Bloom"
        return RunStatus("succeeded", current, step_pods, 0, message)

    if current is None:
        return None
    return RunStatus("running", current, step_pods)


def step_phases(workflow: dict) -> dict[str, str]:
    """Each started step's own outcome (Argo's phase): a failure of any of its pods, else its
    latest pod's phase. The poller stores it in step_pods under PHASES_KEY for the run page."""
    phases: dict[str, str] = {}
    for step, node in _step_pods(workflow).items():
        phases[step] = node.get("phase") or "Pending"
    for node in (workflow.get("status") or {}).get("nodes", {}).values():
        step = CELLRANGER_STEPS.get(_template(node))
        if node.get("type") == "Pod" and step and node.get("phase") in _FAILED_PHASES:
            phases[step] = node["phase"]
    return phases


def _go_note(pods: dict[str, dict], phases: dict[str, str]) -> str:
    """What the run's message adds about GO and pathways once the dataset has loaded."""
    for step in GO_STEPS:
        if phases.get(step) in _FAILED_PHASES:
            code = _exit_code(pods[step]) if step in pods else None
            reason = f"the {step} step failed" + (f" (exit {code})" if code is not None else "")
            return f"; GO & pathways didn't finish: {reason}"
    if phases.get("go-pathways") == "Succeeded":
        return ", with GO & pathways"
    return ""
