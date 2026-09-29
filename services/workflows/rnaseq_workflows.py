"""
RNA-seq workflow types the rnaseq worker dispatches to Argo.

Every type's runs share the rnaseq_runs table and the rnaseq_dispatch queue, so the
worker claims, completes and fails them with the same three database functions. A
type only says how to build the Argo Workflow for one claimed run, which carries
`run_id`, `workflow_type`, `params`, `run_key` and `msg_id`.
"""

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass

import k8s_client
from k8s_client import K8sConfigError


# The shared dispatch functions from the rnaseq_runs migration.
CLAIM_FN = "claim_rnaseq_run"
COMPLETE_FN = "complete_rnaseq_run"
FAIL_FN = "fail_rnaseq_run"


@dataclass(frozen=True)
class WorkflowType:
    # The rnaseq_runs.workflow_type value this entry handles.
    name: str
    build_body: Callable[[dict], dict]


# Registered once in runai-busch-lab from argo/scrna/cellranger/cellranger-count-template.yaml.
CELLRANGER_TEMPLATE = "cellranger-count-template"
# Step pods report results as this account; the same as cellranger-count-workflow.yaml.
STEP_SERVICE_ACCOUNT = "bloom-workflow"
# The busch-lab Run:ai credential bloom-ghcr-pull.
IMAGE_PULL_SECRET = "dockerregistry-bloom-ghcr-pull"

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
            "arguments": {
                "parameters": [
                    {"name": "sample", "value": run["params"]["sample"]},
                    {"name": "reference", "value": run["params"]["reference"]},
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


CELLRANGER = WorkflowType(name="scrna-cellranger", build_body=build_cellranger_body)

# Keyed by workflow_type; a claimed run of a type missing here is failed.
WORKFLOW_TYPES: dict[str, WorkflowType] = {wf.name: wf for wf in (CELLRANGER,)}
