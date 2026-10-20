"""
RNA-seq workflow types the rnaseq worker dispatches to Argo.

Every type's runs share the rnaseq_runs table and the rnaseq_dispatch queue, so the
worker claims, completes and fails them with the same three database functions. A
type only says how to build the Argo Workflow for one claimed run, which carries
`run_id`, `workflow_type`, `params`, `run_key` and `msg_id`, and what the worker looked up
for it (`dataset`, `genome`). A run that can't be built raises UnrunnableRun, and the
worker fails it with that message.
"""

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass

import k8s_client
from k8s_client import K8sConfigError
from rnaseq_status import RunStatus, read_cellranger_status


# The shared dispatch functions from the rnaseq_runs migration.
CLAIM_FN = "claim_rnaseq_run"
COMPLETE_FN = "complete_rnaseq_run"
FAIL_FN = "fail_rnaseq_run"


class UnrunnableRun(Exception):
    """A claimed run that can never be built; its message is shown on the run's page."""


@dataclass(frozen=True)
class WorkflowType:
    # The rnaseq_runs.workflow_type value this entry handles.
    name: str
    build_body: Callable[[dict], dict]
    # Reads the run's status from its Workflow (Workflow, run row); None = nothing to report yet.
    read_status: Callable[[dict, dict], RunStatus | None]


# The WorkflowTemplate from argo/scrna/cellranger/cellranger-count-template.yaml. Prod and
# staging share runai-busch-lab, so each registers its own copy and names it here, which lets
# staging run a newer template and image than prod.
DEFAULT_CELLRANGER_TEMPLATE = "cellranger-count-template"
CELLRANGER_TEMPLATE = (
    os.environ.get("WORKFLOWS_RNASEQ_CELLRANGER_TEMPLATE", "").strip()
    or DEFAULT_CELLRANGER_TEMPLATE
)
# Step pods report results as this account; the same as cellranger-count-workflow.yaml.
STEP_SERVICE_ACCOUNT = "bloom-workflow"
# The busch-lab Run:ai credential bloom-ghcr-pull.
IMAGE_PULL_SECRET = "dockerregistry-bloom-ghcr-pull"
# The volume RNA-seq steps mount to sign in to Bloom (bloomctl's credentials.txt): this
# environment's pipeline Secret, the cylinder pipeline's too.
BLOOM_CREDENTIALS_VOLUME = "bloom-credentials"

_DNS_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

# How long Argo keeps a finished RNA-seq Workflow and its step pods (and so their logs).
DEFAULT_TTL_SECONDS = 86400


def _resolve_ttl_seconds() -> int:
    """WORKFLOWS_RNASEQ_TTL_SECONDS, or 24 hours if unset or not a positive integer."""
    try:
        value = int(os.environ.get("WORKFLOWS_RNASEQ_TTL_SECONDS", DEFAULT_TTL_SECONDS))
    except ValueError:
        return DEFAULT_TTL_SECONDS
    return value if value > 0 else DEFAULT_TTL_SECONDS


TTL_SECONDS = _resolve_ttl_seconds()


def cellranger_workflow_name(run: dict) -> str:
    """A fixed name per run, so resubmitting the same run is refused by Argo.

    The run_key hash keeps names distinct if a database reset reuses a run id.
    """
    env = k8s_client.ENV_LABEL
    if not _DNS_LABEL.match(env):
        raise K8sConfigError(
            f"K8s client not configured: WORKFLOWS_K8S_ENV_LABEL {env!r} is not "
            "a lowercase DNS label"
        )
    digest = hashlib.sha256(run["run_key"].encode()).hexdigest()[:8]
    return f"scrna-cellranger-{env}-{run['run_id']}-{digest}"


def _task(
    name: str, template: str, parameters: dict[str, str], depends: str | None = None
):
    task = {
        "name": name,
        "templateRef": {"name": CELLRANGER_TEMPLATE, "template": template},
        "arguments": {
            "parameters": [
                {"name": key, "value": f"{{{{workflow.parameters.{value}}}}}"}
                for key, value in parameters.items()
            ]
        },
    }
    if depends:
        task["depends"] = depends
    return task


def _bloom_credentials_volume() -> dict:
    """This environment's pipeline Secret, or an empty folder where none is configured
    (dev), so a step that mounts it always starts. Optional, unlike the cylinder's
    (k8s_client), because log uploads are best effort: a step without credentials still
    runs. A step that can't do without them checks for the file itself."""
    if k8s_client.PIPELINE_SECRET_NAME:
        return {
            "name": BLOOM_CREDENTIALS_VOLUME,
            "secret": {"secretName": k8s_client.PIPELINE_SECRET_NAME, "optional": True},
        }
    return {"name": BLOOM_CREDENTIALS_VOLUME, "emptyDir": {}}


# Shown on the page of a run started without a genome version from Bloom.
NO_GENOME_VERSION = (
    "This run has no genome version from Bloom, which runs now build their reference from; "
    "start it again and choose a genome"
)
# Shown on the page of a run started on a registered sample's name alone, which the pipeline no
# longer reads.
NO_READ_SOURCE = (
    "This run names no S3 folder or SRA run IDs to read from; start it again with one"
)


def build_cellranger_body(run: dict) -> dict:
    """One sample through the template's sample-pipeline, which downloads the run's genome
    version from Bloom and builds its reference alongside getting and staging the reads.

    `genome` is the run's recorded genome version as <genome>.v<N>; a run without one can't
    be built. A run with SRA run IDs passes them, and the pipeline's fetch-sra step downloads
    them into the run's folder, alongside the genome steps. A run with an S3 folder passes the
    folder and the files recorded from it, which the stage step copies; a run with neither
    can't be built. A
    run naming a dataset passes its name and species, which the load-dataset step loads the
    result into Bloom under, and the run's id, which the step tags the dataset with so the
    run is linked to it."""
    genome = run.get("genome")
    if not genome:
        raise UnrunnableRun(NO_GENOME_VERSION)
    sra_runs = run["params"].get("sra_runs")
    fastq_url = run["params"].get("fastq_url")
    if not sra_runs and not fastq_url:
        raise UnrunnableRun(NO_READ_SOURCE)
    parameters = [
        {"name": "sample", "value": run["params"]["sample"]},
        {"name": "reference", "value": genome},
        {"name": "run-id", "value": run["run_key"]},
    ]
    sample_inputs = {"sample": "sample", "reference": "reference", "run-id": "run-id"}
    if fastq_url:
        parameters += [
            {"name": "fastq-url", "value": fastq_url},
            {"name": "fastq-files", "value": json.dumps(run["params"]["fastq_files"])},
        ]
        sample_inputs |= {"fastq-url": "fastq-url", "fastq-files": "fastq-files"}
    # Every run from Bloom passes its id: its reports are stored under it, and its dataset, if
    # it loads one, is tagged with it.
    parameters.append({"name": "bloom-run-id", "value": str(run["run_id"])})
    sample_inputs |= {"bloom-run-id": "bloom-run-id"}
    dataset = run.get("dataset")
    if dataset:
        parameters += [
            {"name": "dataset-name", "value": dataset["name"]},
            {"name": "species-name", "value": dataset["species"]},
        ]
        sample_inputs |= {
            "dataset-name": "dataset-name",
            "species-name": "species-name",
        }
        # The species' KEGG pathways in the run's GO and pathway annotation; none: GO only.
        if dataset.get("kegg_org"):
            parameters.append({"name": "kegg-org", "value": dataset["kegg_org"]})
            sample_inputs |= {"kegg-org": "kegg-org"}
    if sra_runs:
        # fetch-sra reads the run IDs comma-separated, in lane order.
        parameters.append({"name": "sra-runs", "value": ",".join(sra_runs)})
        sample_inputs |= {"sra-runs": "sra-runs"}
    tasks = [_task("sample", "sample-pipeline", sample_inputs)]
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {
            "name": cellranger_workflow_name(run),
            "namespace": k8s_client.NAMESPACE,
            "labels": {
                "project": "busch-lab",
                "submitted-by": "bloom-pipeline",
                "workflow-type": "scrna-cellranger",
                "rnaseq-run-id": str(run["run_id"]),
                "environment": k8s_client.ENV_LABEL,
            },
            # run_key can exceed the 63-character label limit.
            "annotations": {"bloom.salk.edu/run-key": run["run_key"]},
        },
        "spec": {
            "entrypoint": "main",
            "serviceAccountName": STEP_SERVICE_ACCOUNT,
            "imagePullSecrets": [{"name": IMAGE_PULL_SECRET}],
            "ttlStrategy": {"secondsAfterCompletion": TTL_SECONDS},
            "arguments": {"parameters": parameters},
            "volumes": [_bloom_credentials_volume()],
            "templates": [{"name": "main", "dag": {"tasks": tasks}}],
        },
    }


CELLRANGER = WorkflowType(
    name="scrna-cellranger",
    build_body=build_cellranger_body,
    read_status=read_cellranger_status,
)

# Keyed by workflow_type; a claimed run of a type missing here is failed.
WORKFLOW_TYPES: dict[str, WorkflowType] = {wf.name: wf for wf in (CELLRANGER,)}
