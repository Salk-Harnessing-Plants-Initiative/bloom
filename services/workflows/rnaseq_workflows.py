"""
RNA-seq workflow types the rnaseq worker dispatches to Argo.

Each type names its claim/complete/fail database functions and builds the Workflow
body for one claimed run. Every claim returns at least `run_id` and `msg_id`;
complete takes (p_run_id, p_msg_id, p_argo_workflow_name) and fail takes
(p_run_id, p_msg_id, p_message).
"""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass

import k8s_client
from k8s_client import K8sConfigError


@dataclass(frozen=True)
class WorkflowType:
    name: str
    claim_fn: str
    complete_fn: str
    fail_fn: str
    build_body: Callable[[dict], dict]


# Registered once in runai-busch-lab from argo/scrna/cellranger/cellranger-count-template.yaml.
CELLRANGER_TEMPLATE = "cellranger-count-template"
# Step pods report results as this account; the same as cellranger-count-workflow.yaml.
STEP_SERVICE_ACCOUNT = "bloom-workflow"
# The busch-lab Run:ai credential bloom-ghcr-pull.
IMAGE_PULL_SECRET = "dockerregistry-bloom-ghcr-pull"

_DNS_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


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


def build_cellranger_body(run: dict) -> dict:
    """One sample through the template: stage-reference, then sample-pipeline."""
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
                "scrna-run-id": str(run["run_id"]),
                "environment": k8s_client.ENV_LABEL,
            },
            # run_key can exceed the 63-character label limit.
            "annotations": {"bloom.salk.edu/run-key": run["run_key"]},
        },
        "spec": {
            "entrypoint": "main",
            "serviceAccountName": STEP_SERVICE_ACCOUNT,
            "imagePullSecrets": [{"name": IMAGE_PULL_SECRET}],
            "ttlStrategy": {"secondsAfterCompletion": k8s_client.TTL_SECONDS},
            "arguments": {
                "parameters": [
                    {"name": "sample", "value": run["sample"]},
                    {"name": "reference", "value": run["reference"]},
                    {"name": "run-id", "value": run["run_key"]},
                ]
            },
            "templates": [
                {
                    "name": "main",
                    "dag": {
                        "tasks": [
                            {
                                "name": "stage-reference",
                                "templateRef": {
                                    "name": CELLRANGER_TEMPLATE,
                                    "template": "stage-reference",
                                },
                                "arguments": {
                                    "parameters": [
                                        {
                                            "name": "reference",
                                            "value": "{{workflow.parameters.reference}}",
                                        }
                                    ]
                                },
                            },
                            {
                                "name": "sample",
                                "depends": "stage-reference",
                                "templateRef": {
                                    "name": CELLRANGER_TEMPLATE,
                                    "template": "sample-pipeline",
                                },
                                "arguments": {
                                    "parameters": [
                                        {
                                            "name": "sample",
                                            "value": "{{workflow.parameters.sample}}",
                                        },
                                        {
                                            "name": "reference",
                                            "value": "{{workflow.parameters.reference}}",
                                        },
                                        {
                                            "name": "run-id",
                                            "value": "{{workflow.parameters.run-id}}",
                                        },
                                    ]
                                },
                            },
                        ]
                    },
                }
            ],
        },
    }


CELLRANGER = WorkflowType(
    name="scrna-cellranger",
    claim_fn="claim_scrna_cellranger_run",
    complete_fn="complete_scrna_cellranger_run",
    fail_fn="fail_scrna_cellranger_run",
    build_body=build_cellranger_body,
)

WORKFLOW_TYPES: tuple[WorkflowType, ...] = (CELLRANGER,)
