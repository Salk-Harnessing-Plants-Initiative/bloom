"""
Submit an A4 sleap-roots pipeline batch to Argo as a Kubernetes `Workflow` CRD,
via a direct REST call to the Kubernetes API server (`:6443`) — not the `argo`
CLI, not the in-cluster-only Argo Server (`:8888`).

Credentials come from env (WORKFLOWS_K8S_TOKEN / _CA_CERT / _API_URL), mirroring
supabase_client.py's pattern: module-level reads, an eager all-present check
before any network call (raising K8sConfigError, a service misconfiguration —
not a caller error), and a submission failure wrapped as K8sSubmissionError
with a generic message (the real detail is logged server-side only — this
service's `error_message` column is user-facing).

WORKFLOWS_K8S_NAMESPACE and WORKFLOWS_K8S_TTL_SECONDS are plain config values
with safe defaults (`runai-busch-lab`, `3600`) — unlike the three credentials,
neither is ever treated as "missing".
"""

import logging
import os
import ssl

import httpx

logger = logging.getLogger(__name__)


def _resolve_namespace() -> str:
    return os.environ.get("WORKFLOWS_K8S_NAMESPACE", "runai-busch-lab")


def _resolve_ttl_seconds() -> int:
    """Never raises — NAMESPACE/TTL_SECONDS/ENV_LABEL are all "never missing"
    plain config (see module docstring), so a present-but-malformed value
    (e.g. an empty string from a `docker-compose --env-file` misconfiguration
    — a real failure mode this repo's tasks.md already warns about for
    NAMESPACE) must degrade to the same safe default an unset value gets, not
    raise ValueError at MODULE IMPORT time. An uncaught exception here would
    crash dispatch_worker.py before it even installs its SIGTERM/SIGINT
    handlers — exactly the crash-loop-on-startup class of bug
    _connect_with_retry() was built to prevent for a Supabase outage."""
    raw = os.environ.get("WORKFLOWS_K8S_TTL_SECONDS", "3600")
    try:
        return int(raw)
    except ValueError:
        return 3600


def _resolve_env_label() -> str:
    return os.environ.get("WORKFLOWS_K8S_ENV_LABEL", "dev")


TOKEN = os.environ.get("WORKFLOWS_K8S_TOKEN")
CA_CERT = os.environ.get("WORKFLOWS_K8S_CA_CERT")
API_URL = os.environ.get("WORKFLOWS_K8S_API_URL")
NAMESPACE = _resolve_namespace()
TTL_SECONDS = _resolve_ttl_seconds()
# prod and staging deliberately share one namespace (see NAMESPACE above) and
# both run_id sequences start at 1 — this label is what lets a future
# reconciliation sweep (design.md's Risks) tell which database a given
# pipeline-run-id belongs to. Plain config, not a credential: same
# never-"missing" treatment as NAMESPACE/TTL_SECONDS.
ENV_LABEL = _resolve_env_label()

# The four templates a batch's DAG references, in submission order.
_TEMPLATE_REFS = [
    ("images-downloader", "sleap-roots-images-downloader-template"),
    ("predictor", "sleap-roots-predictor-template"),
    ("trait-extractor", "sleap-roots-trait-extractor-template"),
    ("write-back", "sleap-roots-write-back-template"),
]


class K8sConfigError(Exception):
    """A required K8s credential is missing — a service misconfiguration."""


class K8sSubmissionError(Exception):
    """A genuine submission attempt to the K8s API failed (non-2xx or
    network-level). Always constructed with a fixed, generic message — never
    the raw response body or exception text, which may contain the real API
    server URL or other internal detail."""


def _validate_config() -> None:
    missing = [
        name
        for name, val in [
            ("WORKFLOWS_K8S_TOKEN", TOKEN),
            ("WORKFLOWS_K8S_CA_CERT", CA_CERT),
            ("WORKFLOWS_K8S_API_URL", API_URL),
        ]
        if not val
    ]
    if missing:
        raise K8sConfigError(
            f"dispatch worker not configured: missing {', '.join(missing)}"
        )


def _ssl_context() -> ssl.SSLContext:
    """WORKFLOWS_K8S_CA_CERT's PEM content is stored with literal `\\n`
    escape sequences (this repo's env-injection pipeline is line-oriented and
    cannot carry a real multi-line value) — unescape before handing it to
    `ssl.create_default_context`, which wants real newlines."""
    pem = CA_CERT.replace("\\n", "\n")
    return ssl.create_default_context(cadata=pem)


def build_workflow_body(run_id, batch_index: int, scan_ids: list[int]) -> dict:
    """Construct the Workflow CRD body for one batch — a DAG referencing the
    four already-registered WorkflowTemplates in sequence, parameterized by
    this batch's own scan-ids (not the whole run's)."""
    tasks = []
    for i, (task_name, template_name) in enumerate(_TEMPLATE_REFS):
        task = {
            "name": task_name,
            "templateRef": {"name": template_name, "template": task_name},
        }
        if i > 0:
            task["dependencies"] = [_TEMPLATE_REFS[i - 1][0]]
        tasks.append(task)

    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {
            "generateName": "sleap-roots-pipeline-",
            "labels": {
                "submitted-by": "bloom-pipeline",
                "pipeline-run-id": str(run_id),
                "batch-index": str(batch_index),
                "environment": ENV_LABEL,
            },
        },
        "spec": {
            "entrypoint": "pipeline",
            "serviceAccountName": "bloom-workflow",
            "ttlStrategy": {"secondsAfterCompletion": TTL_SECONDS},
            "arguments": {
                "parameters": [
                    {
                        "name": "scan-ids",
                        "value": ",".join(str(sid) for sid in scan_ids),
                    }
                ]
            },
            "templates": [
                {
                    "name": "pipeline",
                    "dag": {"tasks": tasks},
                }
            ],
        },
    }


def submit_workflow(body: dict) -> str:
    """POST the Workflow CRD to the K8s API server; return the generated
    Workflow name on success. Raises K8sConfigError if credentials are
    missing (before any network call), or K8sSubmissionError on any non-2xx
    response or network-level failure."""
    _validate_config()
    url = f"{API_URL}/apis/argoproj.io/v1alpha1/namespaces/{NAMESPACE}/workflows"

    try:
        with httpx.Client(verify=_ssl_context(), timeout=15.0) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except Exception as exc:
        logger.warning("k8s_client: submission request failed: %s", exc)
        raise K8sSubmissionError("Argo Workflow submission failed") from exc

    if resp.status_code // 100 != 2:
        logger.warning(
            "k8s_client: submission rejected (%s): %s", resp.status_code, resp.text
        )
        raise K8sSubmissionError("Argo Workflow submission failed")

    try:
        return resp.json()["metadata"]["name"]
    except (KeyError, TypeError, ValueError) as exc:
        # A 2xx response that doesn't carry metadata.name (malformed body, an
        # unexpected proxy/admission-webhook mutation). Must raise the same
        # K8sSubmissionError every other failure path raises — an uncaught
        # exception here would skip process_one()'s except clauses entirely,
        # leaving the claim to blindly redeliver and resubmit against the
        # real cluster instead of settling as a failed batch.
        logger.warning(
            "k8s_client: submission returned %s but response body was unparseable: %s",
            resp.status_code,
            exc,
        )
        raise K8sSubmissionError("Argo Workflow submission failed") from exc
