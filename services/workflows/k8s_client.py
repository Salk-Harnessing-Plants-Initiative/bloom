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

The submitted `Workflow`'s `spec` is loaded from a vendored, CI-drift-checked
copy of `sleap-roots-pipeline`'s canonical `sleap-roots-pipeline.yaml`
(`vendored/sleap-roots-pipeline.yaml`, pin recorded in the sibling
`SLEAP_ROOTS_PIPELINE_REF`), not hand-built field by field — a prior
hand-reconstruction silently dropped `spec.volumes` entirely and broke every
real batch dispatch (bloom #737). `build_workflow_body` re-reads and re-parses
that file on every call (no caching) and applies exactly four overrides on top
of it: the batch's `scan-ids` value, attribution labels (merged, not replacing
whatever the vendored file already sets), `ttlStrategy` (dispatch-only — never
folded into the shared file, since the submitting identity has no `delete`
RBAC and would have no other way to ever clean up dispatched Workflows), and
`metadata.namespace` (forced to `WORKFLOWS_K8S_NAMESPACE` — the vendored file
hardcodes its own namespace, and the Kubernetes API rejects a submission whose
body namespace disagrees with the URL's namespace segment, so this keeps
namespace single-sourced with the value the submission URL already uses). A
missing/unparseable/wrong-shaped vendored file, or a structural drift in the
`scan-ids` parameter position, is a `K8sConfigError` — the same treatment as a
missing credential.
"""

import logging
import os
import ssl
from pathlib import Path

import httpx
import yaml

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
    crash dispatch_worker.py (or status_poller.py, this module's second
    consumer) before it even installs its SIGTERM/SIGINT handlers — exactly
    the crash-loop-on-startup class of bug each service's own
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

# Vendored, CI-drift-checked copy of sleap-roots-pipeline's canonical
# sleap-roots-pipeline.yaml (pin recorded in the sibling SLEAP_ROOTS_PIPELINE_REF
# file) — the single source of truth for this Workflow's shape. A module-level
# constant, not inlined, so tests can monkeypatch it onto a missing/malformed
# fixture the same way TOKEN/CA_CERT/API_URL/NAMESPACE already are.
_VENDORED_WORKFLOW_PATH = (
    Path(__file__).parent / "vendored" / "sleap-roots-pipeline.yaml"
)


class K8sConfigError(Exception):
    """A required K8s credential is missing — a service misconfiguration."""


class K8sSubmissionError(Exception):
    """A genuine submission attempt to the K8s API failed (non-2xx or
    network-level). Always constructed with a fixed, generic message — never
    the raw response body or exception text, which may contain the real API
    server URL or other internal detail."""


class K8sStatusError(Exception):
    """A genuine status-check attempt (get_workflow_status) failed for a
    reason other than the Workflow simply not existing (non-404 non-2xx, or
    network-level). Same sanitized-message convention as
    K8sSubmissionError — never the raw response body or exception text."""


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
        raise K8sConfigError(f"K8s client not configured: missing {', '.join(missing)}")


def _ssl_context() -> ssl.SSLContext:
    """WORKFLOWS_K8S_CA_CERT's PEM content is stored with literal `\\n`
    escape sequences (this repo's env-injection pipeline is line-oriented and
    cannot carry a real multi-line value) — unescape before handing it to
    `ssl.create_default_context`, which wants real newlines."""
    pem = CA_CERT.replace("\\n", "\n")
    return ssl.create_default_context(cadata=pem)


def _load_vendored_workflow() -> dict:
    """Read and parse the vendored canonical Workflow, fresh on every call —
    no module-level caching, since `yaml.safe_load` already returns an
    independent object graph each time, making an explicit copy unnecessary.
    Raises K8sConfigError (not a raw FileNotFoundError/YAMLError/KeyError) for
    a missing file, a YAML syntax error, or a structurally-wrong-but-valid
    file (not a mapping, or missing `spec`/`metadata`) — the same treatment
    this module already gives a missing credential."""
    try:
        raw = _VENDORED_WORKFLOW_PATH.read_text()
    except OSError as exc:
        raise K8sConfigError(
            "K8s client not configured: vendored Workflow source is missing"
        ) from exc

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise K8sConfigError(
            "K8s client not configured: vendored Workflow source failed to parse"
        ) from exc

    if (
        not isinstance(parsed, dict)
        or not isinstance(parsed.get("spec"), dict)
        or not isinstance(parsed.get("metadata"), dict)
    ):
        raise K8sConfigError(
            "K8s client not configured: vendored Workflow source has an unexpected shape"
        )

    return parsed


def build_workflow_body(run_id, batch_index: int, scan_ids: list[int]) -> dict:
    """Construct the Workflow CRD body for one batch by loading the vendored
    canonical `sleap-roots-pipeline.yaml` and applying exactly four overrides
    on top of it — see the module docstring for why each one exists. The
    vendored file's DAG (referencing the four already-registered
    WorkflowTemplates), volumes, entrypoint, and serviceAccountName all pass
    through unmodified."""
    body = _load_vendored_workflow()

    parameters = body["spec"]["arguments"]["parameters"]
    if not parameters or parameters[0].get("name") != "scan-ids":
        raise K8sConfigError(
            "K8s client not configured: vendored Workflow source's scan-ids "
            "parameter is missing or has drifted to a different position"
        )
    parameters[0]["value"] = ",".join(str(sid) for sid in scan_ids)

    labels = body["metadata"].setdefault("labels", {})
    labels.update(
        {
            "submitted-by": "bloom-pipeline",
            "pipeline-run-id": str(run_id),
            "batch-index": str(batch_index),
            "environment": ENV_LABEL,
        }
    )

    body["spec"]["ttlStrategy"] = {"secondsAfterCompletion": TTL_SECONDS}
    body["metadata"]["namespace"] = NAMESPACE

    return body


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


def get_workflow_status(name: str) -> str | None:
    """GET a single Workflow's real phase (Pending/Running/Succeeded/Failed/
    Error) by name. Returns None on 404 — the Workflow no longer exists (most
    often ttlStrategy already cleaned it up, an expected condition, not a
    failure). Raises K8sStatusError for any other non-2xx response or
    network-level failure, with a fixed, generic message — the real detail is
    logged server-side only."""
    _validate_config()
    url = f"{API_URL}/apis/argoproj.io/v1alpha1/namespaces/{NAMESPACE}/workflows/{name}"

    try:
        with httpx.Client(verify=_ssl_context(), timeout=15.0) as client:
            resp = client.get(
                url,
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
    except Exception as exc:
        logger.warning("k8s_client: status check request failed: %s", exc)
        raise K8sStatusError("Argo Workflow status check failed") from exc

    if resp.status_code == 404:
        return None

    if resp.status_code // 100 != 2:
        logger.warning(
            "k8s_client: status check rejected (%s): %s", resp.status_code, resp.text
        )
        raise K8sStatusError("Argo Workflow status check failed")

    try:
        return resp.json()["status"]["phase"]
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "k8s_client: status check returned %s but response body was unparseable: %s",
            resp.status_code,
            exc,
        )
        raise K8sStatusError("Argo Workflow status check failed") from exc
