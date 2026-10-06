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
TTL_SECONDS is stamped as each Workflow's ttlStrategy at dispatch, and the
cylinder status poller reads the same value as the earliest a Workflow can have
been garbage-collected after it was dispatched (bloom#1042).

WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT and WORKFLOWS_K8S_PIPELINE_SECRET_NAME are
plain config too, but have no default (bloom#863): prod and staging share one
namespace, and each needs its own stage directories and Supabase credential
Secret. With either missing or invalid, or CYL_PIPELINE_TRIGGER_ENABLED not
exactly "true", `build_workflow_body` raises K8sDispatchRefusedError before
reading the vendored file, and the dispatch worker fails the batch at once.

The submitted `Workflow`'s `spec` is loaded from a vendored, CI-drift-checked
copy of `sleap-roots-pipeline`'s canonical `sleap-roots-pipeline.yaml`
(`vendored/sleap-roots-pipeline.yaml`, pin recorded in the sibling
`SLEAP_ROOTS_PIPELINE_REF`), not hand-built field by field — a prior
hand-reconstruction silently dropped `spec.volumes` entirely and broke every
real batch dispatch (bloom #737). `build_workflow_body` re-reads and re-parses
that file on every call (no caching) and applies exactly six overrides on top
of it: the batch's `scan-ids` value, attribution labels (merged, not replacing
whatever the vendored file already sets), `ttlStrategy` (dispatch-only — never
folded into the shared file, since the submitting identity has no `delete`
RBAC and would have no other way to ever clean up dispatched Workflows), and
`metadata.namespace` (forced to `WORKFLOWS_K8S_NAMESPACE` — the vendored file
hardcodes its own namespace, and the Kubernetes API rejects a submission whose
body namespace disagrees with the URL's namespace segment, so this keeps
namespace single-sourced with the value the submission URL already uses), and
the three stage volumes' `hostPath.path` and `bloom-credentials`' `secretName`
(set from the two PIPELINE_* values, so each environment stages into its own
directories with its own credential — the vendored file names staging's). A
missing/unparseable/wrong-shaped vendored file, a structural drift in the
`scan-ids` parameter position, or a volume set other than those four volumes,
is a `K8sConfigError` — the same treatment as a missing credential.
"""

import logging
import os
import re
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


# An RFC 1123 subdomain: the name rule for a Kubernetes Secret.
_K8S_OBJECT_NAME = re.compile(
    r"[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*"
)


def _resolve_pipeline_hostpath_root() -> tuple[str | None, str | None]:
    """(root, None) for a usable WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT, else
    (None, why). Never raises, for the same reason as _resolve_ttl_seconds; an
    invalid root is reported as a dispatch refusal instead (bloom#863).

    String checks only, never Path/os.path: the root is a path on the cluster's
    nodes, and on Windows Path('/hpi/x').is_absolute() is False, so a dev box
    and CI would disagree about the same value."""
    raw = os.environ.get("WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT")
    if not raw:
        return None, "is unset or blank"
    if any(ch.isspace() or not ch.isprintable() for ch in raw):
        return None, "contains whitespace or a control character"
    if not raw.startswith("/"):
        return None, "is not an absolute path"
    if raw == "/":
        return None, "is the filesystem root"
    if raw.endswith("/"):
        return None, "ends in '/'"
    segments = raw.split("/")[1:]
    if "" in segments:
        return None, "has an empty segment"
    if any(seg in (".", "..") for seg in segments):
        return None, "has a '.' or '..' segment"
    return raw, None


def _resolve_pipeline_secret_name() -> tuple[str | None, str | None]:
    """(name, None) for a valid WORKFLOWS_K8S_PIPELINE_SECRET_NAME, else
    (None, why). fullmatch, not match with `$`, which accepts a trailing
    newline. Never raises (see _resolve_pipeline_hostpath_root)."""
    raw = os.environ.get("WORKFLOWS_K8S_PIPELINE_SECRET_NAME")
    if not raw:
        return None, "is unset or blank"
    if len(raw) > 253 or not _K8S_OBJECT_NAME.fullmatch(raw):
        return None, "is not a valid Kubernetes object name"
    return raw, None


def _resolve_pipeline_dispatch_enabled() -> bool:
    """The same switch, and the same rule, as bloom-web's
    isPipelineTriggerEnabled (web/lib/cyl-pipeline/trigger-enabled.ts): on only
    for exactly "true". bloom-web reads it per request; this module at import."""
    return os.environ.get("CYL_PIPELINE_TRIGGER_ENABLED") == "true"


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
# Each environment's own stage root and credential Secret, which
# build_workflow_body puts in place of the vendored file's staging values,
# and the switch that lets it dispatch at all (bloom#863). No defaults: an
# environment that sets none of them refuses rather than falling back to
# another environment's directories. The *_INVALID reasons are logged when a
# batch is refused, since logging here would run before logging is set up.
PIPELINE_HOSTPATH_ROOT, PIPELINE_HOSTPATH_ROOT_INVALID = (
    _resolve_pipeline_hostpath_root()
)
PIPELINE_SECRET_NAME, PIPELINE_SECRET_NAME_INVALID = _resolve_pipeline_secret_name()
PIPELINE_DISPATCH_ENABLED = _resolve_pipeline_dispatch_enabled()

# Vendored, CI-drift-checked copy of sleap-roots-pipeline's canonical
# sleap-roots-pipeline.yaml (pin recorded in the sibling SLEAP_ROOTS_PIPELINE_REF
# file) — the single source of truth for this Workflow's shape. A module-level
# constant, not inlined, so tests can monkeypatch it onto a missing/malformed
# fixture the same way TOKEN/CA_CERT/API_URL/NAMESPACE already are.
_VENDORED_WORKFLOW_PATH = (
    Path(__file__).parent / "vendored" / "sleap-roots-pipeline.yaml"
)

# Each vendored stage volume and the sub-directory of PIPELINE_HOSTPATH_ROOT it
# gets. The names match the vendored a4_poc layout, so staging's root yields
# exactly the vendored paths.
_STAGE_SUBDIRS = {
    "images-input-dir": "input",
    "predictions-output-dir": "predictions",
    "traits-output-dir": "traits",
}


class K8sConfigError(Exception):
    """A required K8s credential is missing, or the vendored Workflow source
    is missing or has drifted — a service misconfiguration, left unsettled
    for redelivery by the dispatch worker."""


class K8sDispatchRefusedError(Exception):
    """This environment may not dispatch: its switch is off (`reason="off"`),
    or its stage root or credential Secret is missing or invalid
    (`reason="unconfigured"`). A deliberate state, not a submission attempt:
    the dispatch worker fails the batch at once with a fixed message chosen
    from `reason`, never from this exception's text."""

    REASONS = ("off", "unconfigured")

    def __init__(self, reason: str, detail: str = ""):
        if reason not in self.REASONS:
            raise ValueError(f"unknown dispatch refusal reason {reason!r}")
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class K8sSubmissionError(Exception):
    """A genuine submission attempt to the K8s API failed (non-2xx or
    network-level). Always constructed with a fixed, generic message — never
    the raw response body or exception text, which may contain the real API
    server URL or other internal detail."""


class K8sAlreadyExistsError(K8sSubmissionError):
    """The API refused the submission because a Workflow with the body's
    `metadata.name` already exists (409). Only a caller that sets a fixed name
    sees this; a `generateName` body never collides."""


class K8sStatusError(Exception):
    """A genuine status-check attempt (get_workflow_status) failed for a
    reason other than the Workflow simply not existing: a non-2xx response
    (including a 404 that is not a verified NotFound for that Workflow), a
    network-level failure or an unparseable body. Same sanitized-message
    convention as K8sSubmissionError — never the raw response body or
    exception text."""


class K8sPodNotRunningError(K8sStatusError):
    """The pod exists but its container is not running yet (the log API answers 400:
    waiting to start, or not yet placed on a node), so it has no log to read."""


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
    a missing file, a symlinked file (could point a future edit somewhere the
    CI drift-check's path-scoped git diff would never notice), a YAML syntax
    error, or a structurally-wrong-but-valid file (not a mapping, or missing
    `spec`/`metadata`) — the same treatment this module already gives a
    missing credential."""
    if _VENDORED_WORKFLOW_PATH.is_symlink():
        raise K8sConfigError(
            "K8s client not configured: vendored Workflow source must not be a symlink"
        )

    try:
        # encoding= is explicit, not incidental: bare read_text() decodes with the
        # platform locale (cp1252 on a Windows dev box, UTF-8 in the container),
        # and this file carries non-ASCII characters. Harmless while they all sit
        # in comments that safe_load drops, but a silent Windows/Linux divergence
        # the moment upstream puts one in a value.
        raw = _VENDORED_WORKFLOW_PATH.read_text(encoding="utf-8")
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
    canonical `sleap-roots-pipeline.yaml` and applying exactly six overrides
    on top of it — see the module docstring for why each one exists. Refuses
    first (K8sDispatchRefusedError, before the file is read) when this
    environment is switched off or unconfigured. The vendored file's DAG
    (referencing the five already-registered WorkflowTemplates), entrypoint,
    serviceAccountName and volume set pass through unmodified; only the three
    stage paths and the credential Secret's name change within the volumes."""
    _refuse_unless_dispatch_allowed()
    body = _load_vendored_workflow()

    # .get() chains, not body["spec"]["arguments"]["parameters"] directly:
    # `_load_vendored_workflow` only validates that `spec`/`metadata` are
    # dicts, not their nested structure, so a vendored file missing
    # `arguments`/`parameters` entirely (as opposed to having them but
    # misnamed) must still raise K8sConfigError, not a raw KeyError. The
    # isinstance checks below cover the shape being present but wrong (not a
    # list, or its first element not a dict) — otherwise `.get("name")` on a
    # non-dict element raises a raw AttributeError/TypeError instead.
    parameters = body.get("spec", {}).get("arguments", {}).get("parameters")
    if (
        not isinstance(parameters, list)
        or not parameters
        or not isinstance(parameters[0], dict)
        or parameters[0].get("name") != "scan-ids"
    ):
        raise K8sConfigError(
            "K8s client not configured: vendored Workflow source's scan-ids "
            "parameter is missing or has drifted to a different position"
        )
    parameters[0]["value"] = ",".join(str(sid) for sid in scan_ids)

    dispatch_labels = {
        "submitted-by": "bloom-pipeline",
        "pipeline-run-id": str(run_id),
        "batch-index": str(batch_index),
        "environment": ENV_LABEL,
    }
    labels = body["metadata"].setdefault("labels", {})
    collisions = set(labels) & set(dispatch_labels)
    if collisions:
        # A plain dict merge would let the dispatch value silently win with
        # no signal — the same class of silent-coupling bug the scan-ids
        # assertion above exists to catch, just for labels instead.
        raise K8sConfigError(
            "K8s client not configured: vendored Workflow source already "
            f"defines label(s) {sorted(collisions)} that this worker also sets"
        )
    labels.update(dispatch_labels)

    body["spec"]["ttlStrategy"] = {"secondsAfterCompletion": TTL_SECONDS}
    body["metadata"]["namespace"] = NAMESPACE

    # This environment's own stage directories and credential Secret, in
    # place of the vendored file's staging values (bloom#863). In place and
    # by name, so order and every other field are kept.
    for volume in _stage_and_credential_volumes(body):
        if volume["name"] in _STAGE_SUBDIRS:
            subdir = _STAGE_SUBDIRS[volume["name"]]
            volume["hostPath"]["path"] = f"{PIPELINE_HOSTPATH_ROOT}/{subdir}"
        else:
            volume["secret"]["secretName"] = PIPELINE_SECRET_NAME

    return body


def _refuse_unless_dispatch_allowed() -> None:
    """Raise K8sDispatchRefusedError unless this environment is switched on
    and has a valid stage root and credential Secret. The switch first, so an
    environment that is both off and unconfigured reports "off"."""
    if not PIPELINE_DISPATCH_ENABLED:
        raise K8sDispatchRefusedError(
            "off", 'CYL_PIPELINE_TRIGGER_ENABLED is not exactly "true"'
        )
    problems = [
        f"{name} {why}"
        for name, value, why in (
            (
                "WORKFLOWS_K8S_PIPELINE_HOSTPATH_ROOT",
                PIPELINE_HOSTPATH_ROOT,
                PIPELINE_HOSTPATH_ROOT_INVALID,
            ),
            (
                "WORKFLOWS_K8S_PIPELINE_SECRET_NAME",
                PIPELINE_SECRET_NAME,
                PIPELINE_SECRET_NAME_INVALID,
            ),
        )
        if value is None
    ]
    if problems:
        raise K8sDispatchRefusedError("unconfigured", "; ".join(problems))


def _stage_and_credential_volumes(body: dict) -> list[dict]:
    """The vendored `spec.volumes`, checked against a closed contract: exactly
    the three stage hostPaths (each just a path and `type: Directory`) and the
    one credential Secret (just its name), nothing else of any type. A volume added upstream would otherwise be submitted as-is, still
    pointing at storage every environment shares. Raises K8sConfigError, never
    a raw KeyError/TypeError, for anything else."""
    volumes = body["spec"].get("volumes")
    drift = K8sConfigError(
        "K8s client not configured: vendored Workflow source's volumes have "
        "drifted from the three stage hostPaths and one credential Secret"
    )
    if not isinstance(volumes, list) or not all(isinstance(v, dict) for v in volumes):
        raise drift
    names = [v.get("name") for v in volumes]
    if sorted(map(str, names)) != sorted([*_STAGE_SUBDIRS, "bloom-credentials"]):
        raise drift
    for volume in volumes:
        if volume["name"] in _STAGE_SUBDIRS:
            # Exactly a path and `type: Directory`: Directory is what makes a
            # missing directory leave the pod Pending. DirectoryOrCreate would
            # silently create it on the node's local disk, and the output
            # would vanish behind a successful-looking run.
            host_path = volume.get("hostPath")
            if (
                set(volume) != {"name", "hostPath"}
                or not isinstance(host_path, dict)
                or set(host_path) != {"path", "type"}
                or not isinstance(host_path["path"], str)
                or host_path["type"] != "Directory"
            ):
                raise drift
        elif (
            set(volume) != {"name", "secret"}
            or not isinstance(volume.get("secret"), dict)
            # Exactly a name: `optional: true` would start pods with an empty
            # mount when this environment's Secret doesn't exist.
            or set(volume["secret"]) != {"secretName"}
        ):
            raise drift
    return volumes


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

    if resp.status_code == 409:
        logger.info("k8s_client: submission refused, the Workflow already exists")
        raise K8sAlreadyExistsError("Argo Workflow already exists")
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


def _request_workflow(name: str) -> httpx.Response:
    """GET a single Workflow by name and return the raw response. Raises
    K8sConfigError before any network call when credentials are missing, and
    K8sStatusError, with a fixed, generic message, on a network-level failure."""
    _validate_config()
    url = f"{API_URL}/apis/argoproj.io/v1alpha1/namespaces/{NAMESPACE}/workflows/{name}"

    try:
        with httpx.Client(verify=_ssl_context(), timeout=15.0) as client:
            return client.get(
                url,
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
    except Exception as exc:
        logger.warning("k8s_client: status check request failed: %s", exc)
        raise K8sStatusError("Argo Workflow status check failed") from exc


def _parse_workflow(resp: httpx.Response) -> dict:
    """The Workflow object from a response that is not a 404. Raises
    K8sStatusError for any other non-2xx response or a body that is not a JSON
    object; the real detail is logged server-side only."""
    if resp.status_code // 100 != 2:
        logger.warning(
            "k8s_client: status check rejected (%s): %s", resp.status_code, resp.text
        )
        raise K8sStatusError("Argo Workflow status check failed")

    try:
        workflow = resp.json()
    except ValueError as exc:
        logger.warning(
            "k8s_client: status check returned %s but response body was unparseable: %s",
            resp.status_code,
            exc,
        )
        raise K8sStatusError("Argo Workflow status check failed") from exc
    if not isinstance(workflow, dict):
        logger.warning(
            "k8s_client: status check returned %s but the body was not an object",
            resp.status_code,
        )
        raise K8sStatusError("Argo Workflow status check failed")
    return workflow


_WORKFLOW_PHASES = frozenset({"Pending", "Running", "Succeeded", "Failed", "Error"})


def _is_verified_not_found(resp: httpx.Response, name: str) -> bool:
    """Whether a 404 is the Kubernetes API saying this exact Argo Workflow does
    not exist: a `Status` with reason NotFound whose details name it. A proxy's
    page, a missing CRD or a wrong API path also return 404, but not this body
    (fix-cyl-poller-unconcluded-runs, bloom#1042)."""
    try:
        body = resp.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    details = body.get("details")
    return (
        body.get("kind") == "Status"
        and body.get("reason") == "NotFound"
        and isinstance(details, dict)
        and details.get("name") == name
        and details.get("kind") == "workflows"
        and details.get("group") == "argoproj.io"
    )


def get_workflow(name: str) -> dict | None:
    """GET a single Workflow by name and return it whole (metadata, spec and status).
    Returns None only on a verified NotFound (see _is_verified_not_found): the Workflow
    no longer exists, most often because its ttlStrategy cleaned it up. Any other 404
    (a proxy's page, a missing CRD, a wrong path) is logged with its body and raises
    K8sStatusError, as does any other non-2xx response, a network-level failure or an
    unparseable body, with a fixed, generic message."""
    resp = _request_workflow(name)
    if resp.status_code == 404:
        if _is_verified_not_found(resp, name):
            return None
        logger.warning(
            "k8s_client: workflow read got a 404 that does not name workflow %s: %s",
            name,
            resp.text[:500],
        )
        raise K8sStatusError("Argo Workflow status check failed")
    return _parse_workflow(resp)


def get_pod_log(
    pod: str, container: str, tail_lines: int, limit_bytes: int
) -> str | None:
    """The last `tail_lines` lines (at most `limit_bytes`) of one container's log in a
    pod. Returns None on 404: the pod no longer exists. Raises K8sStatusError for any
    other non-2xx response or a network-level failure, with a fixed, generic message."""
    _validate_config()
    url = f"{API_URL}/api/v1/namespaces/{NAMESPACE}/pods/{pod}/log"
    params = {
        "container": container,
        "tailLines": str(tail_lines),
        "limitBytes": str(limit_bytes),
    }

    try:
        with httpx.Client(verify=_ssl_context(), timeout=15.0) as client:
            resp = client.get(
                url,
                headers={"Authorization": f"Bearer {TOKEN}"},
                params=params,
            )
    except Exception as exc:
        logger.warning("k8s_client: pod log request failed: %s", exc)
        raise K8sStatusError("Pod log read failed") from exc

    if resp.status_code == 404:
        return None
    if resp.status_code == 400:
        logger.info("k8s_client: pod log not available yet: %s", resp.text[:500])
        raise K8sPodNotRunningError("Pod is not running yet")
    if resp.status_code // 100 != 2:
        logger.warning(
            "k8s_client: pod log request rejected (%s): %s",
            resp.status_code,
            resp.text[:500],
        )
        raise K8sStatusError("Pod log read failed")
    return resp.text


def get_workflow_status(name: str, run_id: int | None = None) -> str | None:
    """A single Workflow's real phase (Pending/Running/Succeeded/Failed/Error) by name.

    Returns None only when the Workflow is gone for this caller: a verified
    NotFound (see _is_verified_not_found), or, when run_id is given, a Workflow
    whose `pipeline-run-id` label names another run or whose `environment`
    label names another environment (a garbage-collected Workflow's generated
    name can be reused by a later dispatch, from either environment; a missing
    label doesn't count against it). Any other 404 is logged with its body and
    raises K8sStatusError, as does any other failure, including a Workflow
    with no phase or a phase outside Pending/Running/Succeeded/Failed/Error
    (fix-cyl-poller-unconcluded-runs, bloom#1042)."""
    resp = _request_workflow(name)
    if resp.status_code == 404:
        if _is_verified_not_found(resp, name):
            return None
        logger.warning(
            "k8s_client: status check got a 404 that does not name workflow %s: %s",
            name,
            resp.text[:500],
        )
        raise K8sStatusError("Argo Workflow status check failed")
    workflow = _parse_workflow(resp)
    if run_id is not None:
        metadata = workflow.get("metadata")
        labels = metadata.get("labels") if isinstance(metadata, dict) else None
        labels = labels if isinstance(labels, dict) else {}
        owner = labels.get("pipeline-run-id")
        environment = labels.get("environment")
        # Prod and staging share a namespace and both number runs from 1, so a
        # reused name is this run's only if the environment matches too.
        if (owner is not None and owner != str(run_id)) or (
            environment is not None and environment != ENV_LABEL
        ):
            logger.warning(
                "k8s_client: workflow %s belongs to run %s in %s, not run %s in %s",
                name,
                owner,
                environment,
                run_id,
                ENV_LABEL,
            )
            return None
    try:
        phase = workflow["status"]["phase"]
    except (KeyError, TypeError) as exc:
        logger.warning(
            "k8s_client: status check returned a Workflow without a phase: %s", exc
        )
        raise K8sStatusError("Argo Workflow status check failed") from exc
    # Anything else (Argo's empty or "Unknown" phase) is not an outcome: a
    # terminal conclusion built on it would be final, so it is a failed lookup.
    if phase not in _WORKFLOW_PHASES:
        logger.warning(
            "k8s_client: workflow %s reported an unexpected phase %r", name, phase
        )
        raise K8sStatusError("Argo Workflow status check failed")
    return phase
