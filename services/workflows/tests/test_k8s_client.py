"""Unit tests for k8s_client.py — credential validation, Workflow CRD
construction, and the raw K8s REST submission call. Mocks `httpx` the same
way test_auth.py already does for JWT validation (a monkeypatched
`_FakeClient`/`_FakeResp` onto `k8s_client.httpx.Client`) — no real network
call, no real Kubernetes cluster."""

import copy
import datetime

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import k8s_client
from k8s_client import K8sConfigError, K8sStatusError, K8sSubmissionError


@pytest.fixture
def vendored_workflow():
    """The real vendored file, parsed independently of build_workflow_body's
    own internal parsing — the baseline every 'matches the vendored file'
    assertion below compares against."""
    return yaml.safe_load(
        k8s_client._VENDORED_WORKFLOW_PATH.read_text(encoding="utf-8")
    )


class _FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(payload or "")

    def json(self):
        return self._payload


class _FakeClient:
    """Stand-in for httpx.Client used as a context manager."""

    def __init__(self, resp=None, raise_exc=None, capture=None):
        self._resp = resp
        self._raise_exc = raise_exc
        self._capture = capture if capture is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None, **kwargs):
        self._capture["url"] = url
        self._capture["headers"] = headers
        self._capture["json"] = json
        self._capture["kwargs"] = kwargs
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._resp

    def get(self, url, headers=None, **kwargs):
        self._capture["url"] = url
        self._capture["headers"] = headers
        self._capture["kwargs"] = kwargs
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._resp


@pytest.fixture(scope="module")
def sample_pem_cert() -> str:
    """A real, valid self-signed PEM certificate (with real newlines) for
    exercising the escaped-newline-unescape + ssl.SSLContext conversion."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(autouse=True)
def _configured(monkeypatch, sample_pem_cert):
    """Every test starts fully configured; individual tests knock out
    whichever var they're exercising. CA_CERT uses a real, valid PEM (escaped,
    matching how it's actually stored) since submit_workflow always builds a
    real ssl.SSLContext even when httpx.Client itself is mocked."""
    monkeypatch.setattr(k8s_client, "TOKEN", "test-token")
    monkeypatch.setattr(k8s_client, "CA_CERT", sample_pem_cert.replace("\n", "\\n"))
    monkeypatch.setattr(k8s_client, "API_URL", "https://10.7.30.173:6443")
    monkeypatch.setattr(k8s_client, "NAMESPACE", "runai-busch-lab")
    monkeypatch.setattr(k8s_client, "TTL_SECONDS", 3600)


# --- Eager credential validation --------------------------------------------


def test_missing_token_raises_k8sconfigerror_before_any_network_call(monkeypatch):
    monkeypatch.setattr(k8s_client, "TOKEN", None)
    calls = {"posted": False}
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: calls.update(posted=True)
    )
    with pytest.raises(K8sConfigError) as exc:
        k8s_client.submit_workflow({})
    assert "WORKFLOWS_K8S_TOKEN" in str(exc.value)
    assert calls["posted"] is False


def test_k8sconfigerror_message_is_caller_neutral_not_dispatch_worker_specific(
    monkeypatch,
):
    """Found during /review-pr: _validate_config() is shared by
    submit_workflow (dispatch worker) and get_workflow_status (status
    poller), but its message used to hardcode "dispatch worker not
    configured" — misleading whoever is on call when the status poller, not
    the dispatch worker, is what's actually misconfigured. Assert both call
    sites raise a message that doesn't name either specific caller."""
    monkeypatch.setattr(k8s_client, "TOKEN", None)
    with pytest.raises(K8sConfigError) as exc_submit:
        k8s_client.submit_workflow({})
    with pytest.raises(K8sConfigError) as exc_status:
        k8s_client.get_workflow_status("wf-abc")
    assert "dispatch worker" not in str(exc_submit.value).lower()
    assert "dispatch worker" not in str(exc_status.value).lower()


def test_missing_ca_cert_raises_k8sconfigerror_before_any_network_call(monkeypatch):
    monkeypatch.setattr(k8s_client, "CA_CERT", None)
    calls = {"posted": False}
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: calls.update(posted=True)
    )
    with pytest.raises(K8sConfigError) as exc:
        k8s_client.submit_workflow({})
    assert "WORKFLOWS_K8S_CA_CERT" in str(exc.value)
    assert calls["posted"] is False


def test_missing_api_url_raises_k8sconfigerror_before_any_network_call(monkeypatch):
    monkeypatch.setattr(k8s_client, "API_URL", None)
    calls = {"posted": False}
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: calls.update(posted=True)
    )
    with pytest.raises(K8sConfigError) as exc:
        k8s_client.submit_workflow({})
    assert "WORKFLOWS_K8S_API_URL" in str(exc.value)
    assert calls["posted"] is False


# --- Namespace / TTL defaults (not eagerly required) ------------------------


def test_namespace_defaults_to_busch_lab_when_unset(monkeypatch):
    monkeypatch.delenv("WORKFLOWS_K8S_NAMESPACE", raising=False)
    monkeypatch.setattr(k8s_client, "NAMESPACE", k8s_client._resolve_namespace())
    assert k8s_client.NAMESPACE == "runai-busch-lab"
    k8s_client._validate_config()  # must not raise — namespace is never "missing"


def test_ttl_defaults_to_3600_when_unset(monkeypatch):
    monkeypatch.delenv("WORKFLOWS_K8S_TTL_SECONDS", raising=False)
    monkeypatch.setattr(k8s_client, "TTL_SECONDS", k8s_client._resolve_ttl_seconds())
    assert k8s_client.TTL_SECONDS == 3600
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    assert body["spec"]["ttlStrategy"]["secondsAfterCompletion"] == 3600
    k8s_client._validate_config()  # must not raise — TTL is never "missing"


def test_ttl_falls_back_to_3600_on_a_malformed_value_instead_of_raising(monkeypatch):
    """A present-but-empty/malformed WORKFLOWS_K8S_TTL_SECONDS (a real
    docker-compose --env-file misconfiguration, not hypothetical) must not
    raise ValueError at import time — that would crash dispatch_worker.py
    before it even installs its SIGTERM/SIGINT handlers."""
    monkeypatch.setenv("WORKFLOWS_K8S_TTL_SECONDS", "")
    assert k8s_client._resolve_ttl_seconds() == 3600

    monkeypatch.setenv("WORKFLOWS_K8S_TTL_SECONDS", "not-a-number")
    assert k8s_client._resolve_ttl_seconds() == 3600


# --- CA cert: escaped newlines -> real newlines -> ssl.SSLContext ----------


def test_ca_cert_escaped_newlines_are_unescaped_then_converted_to_an_ssl_context(
    monkeypatch, sample_pem_cert
):
    escaped = sample_pem_cert.replace("\n", "\\n")
    monkeypatch.setattr(k8s_client, "CA_CERT", escaped)
    ctx = k8s_client._ssl_context()
    import ssl

    assert isinstance(ctx, ssl.SSLContext)


# --- submit_workflow: request construction ----------------------------------


def test_submit_workflow_posts_to_the_exact_resource_path(monkeypatch):
    capture = {}
    resp = _FakeResp(200, {"metadata": {"name": "sleap-roots-pipeline-abc12"}})
    monkeypatch.setattr(
        k8s_client.httpx,
        "Client",
        lambda *a, **k: _FakeClient(resp=resp, capture=capture),
    )
    k8s_client.submit_workflow({"some": "body"})
    assert (
        capture["url"]
        == "https://10.7.30.173:6443/apis/argoproj.io/v1alpha1/namespaces/runai-busch-lab/workflows"
    )
    assert capture["headers"]["Authorization"] == "Bearer test-token"


def test_submit_workflow_returns_the_generated_name_on_2xx(monkeypatch):
    resp = _FakeResp(201, {"metadata": {"name": "sleap-roots-pipeline-xyz99"}})
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: _FakeClient(resp=resp)
    )
    name = k8s_client.submit_workflow({"some": "body"})
    assert name == "sleap-roots-pipeline-xyz99"


def test_submit_workflow_raises_k8ssubmissionerror_on_4xx_5xx(monkeypatch):
    resp = _FakeResp(
        500, text="internal error at https://10.7.30.173:6443/secret-detail"
    )
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: _FakeClient(resp=resp)
    )
    with pytest.raises(K8sSubmissionError):
        k8s_client.submit_workflow({"some": "body"})


def test_submit_workflow_raises_k8ssubmissionerror_on_2xx_with_unparseable_body(
    monkeypatch,
):
    """A 2xx response whose body doesn't contain metadata.name (a proxy/
    admission-webhook mutation, or a malformed JSON body) must not escape as
    a raw KeyError/JSONDecodeError — dispatch_worker's process_one() only
    catches K8sConfigError/K8sSubmissionError around this call, so anything
    else would bypass fail_batch entirely and leave the claim to blindly
    redeliver and resubmit against the real cluster."""
    resp = _FakeResp(200, {"unexpected": "shape"})
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: _FakeClient(resp=resp)
    )
    with pytest.raises(K8sSubmissionError):
        k8s_client.submit_workflow({"some": "body"})


def test_submit_workflow_raises_k8ssubmissionerror_on_network_error(monkeypatch):
    import httpx as real_httpx

    monkeypatch.setattr(
        k8s_client.httpx,
        "Client",
        lambda *a, **k: _FakeClient(
            raise_exc=real_httpx.ConnectError("connection refused")
        ),
    )
    with pytest.raises(K8sSubmissionError):
        k8s_client.submit_workflow({"some": "body"})


def test_submit_workflow_error_message_is_generic_not_raw(monkeypatch):
    resp = _FakeResp(
        500,
        text="Internal Server Error at https://10.7.30.173:6443/apis/argoproj.io/v1alpha1/...",
    )
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: _FakeClient(resp=resp)
    )
    with pytest.raises(K8sSubmissionError) as exc:
        k8s_client.submit_workflow({"some": "body"})
    message = str(exc.value)
    assert "10.7.30.173" not in message
    assert "Internal Server Error" not in message

    import httpx as real_httpx

    monkeypatch.setattr(
        k8s_client.httpx,
        "Client",
        lambda *a, **k: _FakeClient(
            raise_exc=real_httpx.ConnectError("connection refused to 10.7.30.173:6443")
        ),
    )
    with pytest.raises(K8sSubmissionError) as exc2:
        k8s_client.submit_workflow({"some": "body"})
    assert "10.7.30.173" not in str(exc2.value)


def test_submit_workflow_targets_same_namespace_for_batches_from_different_runs(
    monkeypatch,
):
    captures = []

    def _make_client(*a, **k):
        cap = {}
        captures.append(cap)
        return _FakeClient(
            resp=_FakeResp(200, {"metadata": {"name": "wf-1"}}), capture=cap
        )

    monkeypatch.setattr(k8s_client.httpx, "Client", _make_client)
    k8s_client.submit_workflow(k8s_client.build_workflow_body(1, 0, [1]))
    k8s_client.submit_workflow(k8s_client.build_workflow_body(2, 0, [2]))
    assert len(captures) == 2
    ns_a = captures[0]["url"].split("/namespaces/")[1].split("/")[0]
    ns_b = captures[1]["url"].split("/namespaces/")[1].split("/")[0]
    assert ns_a == ns_b == "runai-busch-lab"


# --- build_workflow_body: CRD shape ------------------------------------------


def test_build_workflow_body_has_correct_apiversion_kind_and_generatename():
    body = k8s_client.build_workflow_body(run_id=7, batch_index=0, scan_ids=[1])
    assert body["apiVersion"] == "argoproj.io/v1alpha1"
    assert body["kind"] == "Workflow"
    assert "generateName" in body["metadata"]
    assert "name" not in body["metadata"]


def test_build_workflow_body_includes_required_labels():
    body = k8s_client.build_workflow_body(run_id=42, batch_index=3, scan_ids=[1, 2])
    labels = body["metadata"]["labels"]
    assert labels["submitted-by"] == "bloom-pipeline"
    assert labels["pipeline-run-id"] == "42"
    assert labels["batch-index"] == "3"


def test_build_workflow_body_includes_environment_label(monkeypatch):
    """prod and staging share the same runai-busch-lab namespace and their
    run_id sequences both start at 1 — without an environment label, a future
    reconciliation sweep (design.md's Risks) can't tell which database a
    given pipeline-run-id belongs to."""
    monkeypatch.setattr(k8s_client, "ENV_LABEL", "staging")
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    assert body["metadata"]["labels"]["environment"] == "staging"


def test_env_label_defaults_to_dev_when_unset(monkeypatch):
    monkeypatch.delenv("WORKFLOWS_K8S_ENV_LABEL", raising=False)
    monkeypatch.setattr(k8s_client, "ENV_LABEL", k8s_client._resolve_env_label())
    assert k8s_client.ENV_LABEL == "dev"
    k8s_client._validate_config()  # must not raise — env label is never "missing"


def test_build_workflow_body_includes_ttl_strategy():
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    assert body["spec"]["ttlStrategy"]["secondsAfterCompletion"] == 3600


def test_build_workflow_body_parameterizes_scan_ids_for_this_batch_only():
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[12, 47, 9])
    params = {p["name"]: p["value"] for p in body["spec"]["arguments"]["parameters"]}
    assert params["scan-ids"] == "12,47,9"


# --- DAG shape (sleap-roots-pipeline #56's exit gate) ----------------------------
#
# Every assertion below is an exact set or an exact sequence, never a spot-check.
# Each weaker form was tried against 58 hand-built mutations of the vendored file
# and demonstrated to miss a real one; the docstrings name which. The byte-level
# CI drift check (scripts/check_vendored_workflow_drift.py) cannot substitute for
# any of this — it goes green whenever the vendored copy and the pin agree with
# each other, including on a DAG of entirely the wrong shape.

#: (task name, templateRef.name, templateRef.template) for the whole DAG, in order.
#: All three together: a pair-only assertion lets `templateRef.template` invoke the
#: wrong inner template out of the right WorkflowTemplate, and a name-*set*
#: assertion lets two task names swap while the templateRef order stays put —
#: which silently re-attributes `predictor-code` to trait-extraction's exit code.
_EXPECTED_DAG = [
    (
        "images-downloader",
        "sleap-roots-images-downloader-template",
        "images-downloader",
    ),
    ("predictor", "sleap-roots-predictor-template", "predictor"),
    ("trait-extractor", "sleap-roots-trait-extractor-template", "trait-extractor"),
    ("write-back", "sleap-roots-write-back-template", "write-back"),
    ("exit-gate", "sleap-roots-exit-gate-template", "exit-gate"),
]

_PRODUCERS = {"images-downloader", "predictor", "trait-extractor"}


def _dag_tasks(body):
    """Resolve the DAG the way Argo does — through `spec.entrypoint` — rather than
    by indexing `spec.templates[0]`.

    Indexing `[0]` passes even when a second, gateless template is appended and the
    entrypoint repointed at it. And without the name check, renaming the `pipeline`
    template while leaving `spec.entrypoint: pipeline` also passes — a mutation that
    makes Argo reject *every* dispatch with "entrypoint pipeline not found".
    """
    spec = body["spec"]
    entrypoint = spec.get("entrypoint")
    assert entrypoint == "pipeline", f"entrypoint is {entrypoint!r}"
    dag_templates = [t for t in spec["templates"] if "dag" in t]
    assert len(dag_templates) == 1, [t.get("name") for t in dag_templates]
    assert dag_templates[0].get("name") == entrypoint, (
        f"the only dag template is {dag_templates[0].get('name')!r} but entrypoint "
        f"is {entrypoint!r} — the entrypoint does not resolve to the DAG"
    )
    return dag_templates[0]["dag"]["tasks"]


def test_build_workflow_body_dag_references_all_five_templates_in_order():
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    tasks = _dag_tasks(body)

    got = []
    for t in tasks:
        ref = t.get("templateRef")
        assert isinstance(ref, dict), f"task {t.get('name')!r} has no templateRef"
        # An exact key set, so a missing `template:` is an AssertionError here
        # rather than a KeyError on the next line.
        assert set(ref) == {"name", "template"}, (
            f"task {t.get('name')!r} templateRef keys: {sorted(ref)}"
        )
        got.append((t.get("name"), ref.get("name"), ref.get("template")))
    assert got == _EXPECTED_DAG

    # dependencies chain matches the order — each task depends on the previous.
    # `deps[0] is None or == []` is deliberate: Argo treats an absent key and an
    # empty list identically, so both are correct for the root.
    deps = [t.get("dependencies") for t in tasks]
    assert deps[0] is None or deps[0] == []
    for i in range(1, len(tasks)):
        assert deps[i] == [tasks[i - 1]["name"]]


def test_build_workflow_body_exit_gate_is_the_only_leaf():
    """Argo's `assessDAGPhase` derives the Workflow phase from the DAG's leaf, so a
    producer that is *also* a leaf would surface its own `Failed` phase and defeat
    `continueOn` entirely.

    The property is "exactly one task is depended on by no other, and it is
    exit-gate" — NOT "no task lists exit-gate in its dependencies", which is
    vacuously true on a DAG that has no exit-gate at all, and also passes when a
    second leaf is bolted on.
    """
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    tasks = _dag_tasks(body)
    names = {t["name"] for t in tasks}
    depended_on = {d for t in tasks for d in (t.get("dependencies") or [])}

    assert names - depended_on == {"exit-gate"}, "exit-gate is not the only leaf"
    assert {t["name"] for t in tasks if not t.get("dependencies")} == {
        "images-downloader"
    }
    # `depends` (the expression form) silently overrides `dependencies`.
    assert all("depends" not in t for t in tasks)


def test_build_workflow_body_continue_on_is_on_producers_only():
    """Asserted as a set equality over task names, not three per-task checks.

    A per-task check does not catch `continueOn` on `exit-gate`, which makes the
    terminal leaf continuable and restores argo-workflows#6396 at the last hop —
    the exact defect the gate exists to prevent. `write-back` deliberately has
    none: its failure omits the gate, which inherits `Failed`.
    """
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    tasks = _dag_tasks(body)
    by_name = {t["name"]: t for t in tasks}

    assert {n for n, t in by_name.items() if "continueOn" in t} == _PRODUCERS
    for name in _PRODUCERS:
        # `==` not `.get()`: {failed: true, error: true} is a different contract,
        # deliberately rejected by the vendored file's own Error-vs-Failed note.
        assert by_name[name]["continueOn"] == {"failed": True}


def test_build_workflow_body_exit_gate_receives_producer_exit_codes():
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    tasks = _dag_tasks(body)

    # Looked up as an assertion, not `next(...)`: that raises StopIteration on a
    # gateless DAG rather than failing, and it would not catch a *duplicated*
    # exit-gate task either.
    gates = [t for t in tasks if t.get("name") == "exit-gate"]
    assert len(gates) == 1, f"exit-gate tasks found: {len(gates)}"

    params = {p["name"]: p["value"] for p in gates[0]["arguments"]["parameters"]}
    assert params == {
        "images-downloader-code": "{{tasks.images-downloader.exitCode}}",
        "predictor-code": "{{tasks.predictor.exitCode}}",
        "trait-extractor-code": "{{tasks.trait-extractor.exitCode}}",
    }
    # Every referenced task must actually exist: renaming a producer while
    # updating only the dependency chain leaves a dangling reference that dag.go
    # substitutes with allowUnresolved=true, so it reaches the gate as a literal.
    referenced = {
        v.removeprefix("{{tasks.").removesuffix(".exitCode}}") for v in params.values()
    }
    assert referenced <= {t["name"] for t in tasks}


def test_build_workflow_body_pins_the_fields_no_other_dag_assertion_covers():
    """Pinned to literals, not to the vendored file.

    Comparing the built body against the same file it was built from is what makes
    the four "preserves ... from_vendored_file" tests below tautological. These are
    the fields a bad upstream re-pin would most plausibly weaken, and until now
    nothing asserted any of them:

    - `type: Directory` (not DirectoryOrCreate) — the vendored file's own comment
      calls this out by name: with DirectoryOrCreate, a node whose NFS mount is
      down silently gets a local directory instead, output vanishes, and the
      pipeline reports success on top of it.
    - `serviceAccountName` — `default` makes every step fail with
      "workflowtaskresults.argoproj.io is forbidden".
    - the task key allowlist — adding `when:` to the gate makes it `Omitted`,
      which `assessDAGPhase` folds into `Succeeded`.
    """
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    spec = body["spec"]

    assert spec.get("serviceAccountName") == "bloom-workflow"
    assert body["metadata"].get("generateName") == "sleap-roots-pipeline-"

    host_paths = {v["name"]: v["hostPath"] for v in spec["volumes"] if "hostPath" in v}
    assert set(host_paths) == {
        "images-input-dir",
        "predictions-output-dir",
        "traits-output-dir",
    }
    assert all(h["type"] == "Directory" for h in host_paths.values())
    secrets = {v["name"]: v["secret"] for v in spec["volumes"] if "secret" in v}
    assert secrets == {
        "bloom-credentials": {
            "secretName": "genericsecret-bloom-staging-pipeline-credentials"
        }
    }

    allowed = {"name", "templateRef", "dependencies", "continueOn", "arguments"}
    for t in _dag_tasks(body):
        assert not set(t) - allowed, (
            f"task {t['name']!r} has unexpected keys {sorted(set(t) - allowed)}"
        )
    assert {t["name"] for t in _dag_tasks(body) if "arguments" in t} == {"exit-gate"}

    assert not set(spec) - {
        "entrypoint",
        "serviceAccountName",
        "arguments",
        "volumes",
        "templates",
        "ttlStrategy",
    }


def _assert_five_task_gate_dag(body):
    """The three gate invariants, as one callable, so the guard is itself guarded
    by the mutation cases below."""
    tasks = _dag_tasks(body)
    names = {t["name"] for t in tasks}
    depended_on = {d for t in tasks for d in (t.get("dependencies") or [])}
    assert names - depended_on == {"exit-gate"}, "exit-gate is not the only leaf"
    with_continue_on = {t["name"] for t in tasks if "continueOn" in t}
    assert with_continue_on == _PRODUCERS, (
        "continueOn is not on exactly the three producers"
    )
    gates = [t for t in tasks if t.get("name") == "exit-gate"]
    assert len(gates) == 1, f"exit-gate tasks found: {len(gates)}"


def _vendored_variant(tmp_path, vendored_workflow, mutate):
    """Write a mutated copy of the vendored file and point the module at it."""
    variant = copy.deepcopy(vendored_workflow)
    mutate(variant["spec"]["templates"][0]["dag"])
    path = tmp_path / "variant.yaml"
    path.write_text(yaml.safe_dump(variant), encoding="utf-8")
    return path


def _drop_gate(dag):
    dag["tasks"] = [t for t in dag["tasks"] if t["name"] != "exit-gate"]


def _strip_continue_on(dag):
    for t in dag["tasks"]:
        t.pop("continueOn", None)


def _gate_beside_write_back(dag):
    """Re-point the gate at trait-extractor, leaving write-back a second leaf."""
    for t in dag["tasks"]:
        if t["name"] == "exit-gate":
            t["dependencies"] = ["trait-extractor"]


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (_drop_gate, "only leaf"),
        (_strip_continue_on, "three producers"),
        (_gate_beside_write_back, "only leaf"),
    ],
    ids=["gateless", "continue_on_stripped", "two_leaves"],
)
def test_the_dag_shape_assertions_reject_a_wrongly_shaped_vendored_file(
    monkeypatch, tmp_path, vendored_workflow, mutate, expected
):
    """The ADDED requirement's "a consistent but wrongly-shaped pair is still
    caught" property, as a standing guard rather than a one-time red.

    A vendored file and its pin bumped together to any of these shapes passes
    `check_vendored_workflow_drift.py` cleanly, so these assertions are the only
    thing between that and production.

    `match=` is load-bearing: a bare `pytest.raises(AssertionError)` is satisfied
    by whichever assertion fires first, which for the gateless case is the
    templateRef-triple check — so the test would still pass with the leaf
    assertion deleted outright. The `two_leaves` case is the only one that
    actually exercises the leaf assertion, since it keeps all five tasks.
    """
    path = _vendored_variant(tmp_path, vendored_workflow, mutate)
    monkeypatch.setattr(k8s_client, "_VENDORED_WORKFLOW_PATH", path)
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    with pytest.raises(AssertionError, match=expected):
        _assert_five_task_gate_dag(body)


# --- build_workflow_body: loaded from the vendored canonical source (bloom #737) --


def test_build_workflow_body_volumes_match_the_vendored_file_exactly(vendored_workflow):
    """Direct regression test for the bug this change fixes: the hand-built
    body silently dropped spec.volumes entirely."""
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    assert body["spec"]["volumes"] == vendored_workflow["spec"]["volumes"]


def test_build_workflow_body_preserves_entrypoint_and_service_account_from_vendored_file(
    vendored_workflow,
):
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    assert body["spec"]["entrypoint"] == vendored_workflow["spec"]["entrypoint"]
    assert (
        body["spec"]["serviceAccountName"]
        == vendored_workflow["spec"]["serviceAccountName"]
    )


def test_build_workflow_body_preserves_dag_structure_from_vendored_file(
    vendored_workflow,
):
    """The DAG-shape equivalent of the entrypoint/serviceAccountName check
    above — a standing regression guard, not just a one-time design.md
    inspection, that the vendored file's DAG still matches what this module
    submits."""
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    actual_tasks = body["spec"]["templates"][0]["dag"]["tasks"]
    expected_tasks = vendored_workflow["spec"]["templates"][0]["dag"]["tasks"]
    assert actual_tasks == expected_tasks


def test_build_workflow_body_only_changes_the_four_documented_overrides(
    monkeypatch, vendored_workflow
):
    """Proves the 'no other field modified' requirement via a full-structure
    diff — the field-specific tests above only spot-check individual paths."""
    monkeypatch.setattr(k8s_client, "NAMESPACE", "runai-busch-lab")
    monkeypatch.setattr(k8s_client, "ENV_LABEL", "staging")
    monkeypatch.setattr(k8s_client, "TTL_SECONDS", 3600)

    body = k8s_client.build_workflow_body(
        run_id=42, batch_index=3, scan_ids=[12, 47, 9]
    )

    expected = copy.deepcopy(vendored_workflow)
    expected["spec"]["arguments"]["parameters"][0]["value"] = "12,47,9"
    expected["metadata"].setdefault("labels", {}).update(
        {
            "submitted-by": "bloom-pipeline",
            "pipeline-run-id": "42",
            "batch-index": "3",
            "environment": "staging",
        }
    )
    expected["spec"]["ttlStrategy"] = {"secondsAfterCompletion": 3600}
    expected["metadata"]["namespace"] = "runai-busch-lab"

    assert body == expected


def test_build_workflow_body_merges_labels_rather_than_replacing(vendored_workflow):
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    labels = body["metadata"]["labels"]
    assert labels["project"] == vendored_workflow["metadata"]["labels"]["project"]
    assert labels["submitted-by"] == "bloom-pipeline"
    assert labels["pipeline-run-id"] == "1"
    assert labels["batch-index"] == "0"
    assert "environment" in labels


def test_build_workflow_body_forces_namespace_to_the_configured_value(
    monkeypatch, vendored_workflow
):
    # Sanity check the premise: the vendored file's hardcoded namespace is
    # what we're about to override, not incidentally the same value.
    assert vendored_workflow["metadata"]["namespace"] == "runai-busch-lab"
    monkeypatch.setattr(k8s_client, "NAMESPACE", "runai-talmo-lab")
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    assert body["metadata"]["namespace"] == "runai-talmo-lab"


def test_build_workflow_body_returns_independent_copies_across_calls():
    """The implementation re-reads and re-parses the vendored file on every
    call rather than caching a parsed structure, which already gives every
    call an independent object graph — this guards against a future change
    that adds caching without also adding a copy step."""
    first = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    second = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    first["spec"]["volumes"].append({"name": "mutated-in-test"})
    assert all(v.get("name") != "mutated-in-test" for v in second["spec"]["volumes"])


def test_build_workflow_body_raises_configerror_on_a_symlinked_vendored_file(
    monkeypatch, tmp_path
):
    """A symlink swap on the vendored path could point future edits
    somewhere the CI drift-check's path-scoped git diff would never notice,
    permanently blinding drift detection from that point on."""
    real_target = tmp_path / "real-target.yaml"
    real_target.write_text("apiVersion: argoproj.io/v1alpha1\n")
    symlinked_vendored = tmp_path / "sleap-roots-pipeline.yaml"
    try:
        symlinked_vendored.symlink_to(real_target)
    except OSError as exc:
        pytest.skip(f"platform/permissions don't allow creating symlinks: {exc}")
    monkeypatch.setattr(k8s_client, "_VENDORED_WORKFLOW_PATH", symlinked_vendored)
    with pytest.raises(K8sConfigError):
        k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])


def test_build_workflow_body_raises_configerror_on_missing_vendored_file(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        k8s_client, "_VENDORED_WORKFLOW_PATH", tmp_path / "does-not-exist.yaml"
    )
    with pytest.raises(K8sConfigError):
        k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])


def test_build_workflow_body_raises_configerror_on_unparseable_vendored_file(
    monkeypatch, tmp_path
):
    """Invalid YAML syntax — distinct from the structurally-wrong-but-valid
    case below."""
    bad_file = tmp_path / "bad.yaml"
    bad_file.write_text("key: [unterminated")
    monkeypatch.setattr(k8s_client, "_VENDORED_WORKFLOW_PATH", bad_file)
    with pytest.raises(K8sConfigError):
        k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])


@pytest.mark.parametrize(
    "content",
    [
        "- just\n- a\n- list\n",  # valid YAML, but not a mapping at all
        "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {}\n",  # missing spec
        "apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nspec: {}\n",  # missing metadata
    ],
)
def test_build_workflow_body_raises_configerror_on_structurally_wrong_vendored_file(
    monkeypatch, tmp_path, content
):
    """Valid YAML with the wrong shape — must not let a raw KeyError/TypeError
    escape from build_workflow_body's own field lookups."""
    wrong_shape_file = tmp_path / "wrong-shape.yaml"
    wrong_shape_file.write_text(content)
    monkeypatch.setattr(k8s_client, "_VENDORED_WORKFLOW_PATH", wrong_shape_file)
    with pytest.raises(K8sConfigError):
        k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])


def test_build_workflow_body_raises_configerror_when_scan_ids_parameter_missing_or_misnamed(
    monkeypatch, tmp_path, vendored_workflow
):
    mutated = copy.deepcopy(vendored_workflow)
    mutated["spec"]["arguments"]["parameters"][0]["name"] = "not-scan-ids"
    mutated_file = tmp_path / "mutated.yaml"
    mutated_file.write_text(yaml.safe_dump(mutated))
    monkeypatch.setattr(k8s_client, "_VENDORED_WORKFLOW_PATH", mutated_file)
    with pytest.raises(K8sConfigError):
        k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])


def test_build_workflow_body_raises_configerror_when_arguments_key_is_entirely_missing(
    monkeypatch, tmp_path, vendored_workflow
):
    """Distinct from the misnamed-parameter case above: here `spec` itself is
    a valid dict (passes `_load_vendored_workflow`'s shape check) but has no
    `arguments` key at all, so indexing `spec["arguments"]["parameters"]`
    must not raise a raw KeyError."""
    mutated = copy.deepcopy(vendored_workflow)
    del mutated["spec"]["arguments"]
    mutated_file = tmp_path / "mutated.yaml"
    mutated_file.write_text(yaml.safe_dump(mutated))
    monkeypatch.setattr(k8s_client, "_VENDORED_WORKFLOW_PATH", mutated_file)
    with pytest.raises(K8sConfigError):
        k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])


def test_build_workflow_body_raises_configerror_when_parameters_key_is_entirely_missing(
    monkeypatch, tmp_path, vendored_workflow
):
    mutated = copy.deepcopy(vendored_workflow)
    del mutated["spec"]["arguments"]["parameters"]
    mutated_file = tmp_path / "mutated.yaml"
    mutated_file.write_text(yaml.safe_dump(mutated))
    monkeypatch.setattr(k8s_client, "_VENDORED_WORKFLOW_PATH", mutated_file)
    with pytest.raises(K8sConfigError):
        k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])


@pytest.mark.parametrize(
    "parameters_value",
    [
        "not-a-list",
        {"name": "scan-ids", "value": ""},
        ["not-a-dict"],
    ],
)
def test_build_workflow_body_raises_configerror_when_parameters_has_the_wrong_shape(
    monkeypatch, tmp_path, vendored_workflow, parameters_value
):
    """Distinct from 'entirely missing' above: here `parameters` IS present
    but isn't a list-of-dicts (a string, a bare mapping, or a list whose
    first element isn't a dict) — `.get("name")` on a non-dict element must
    not raise a raw AttributeError/TypeError."""
    mutated = copy.deepcopy(vendored_workflow)
    mutated["spec"]["arguments"]["parameters"] = parameters_value
    mutated_file = tmp_path / "mutated.yaml"
    mutated_file.write_text(yaml.safe_dump(mutated))
    monkeypatch.setattr(k8s_client, "_VENDORED_WORKFLOW_PATH", mutated_file)
    with pytest.raises(K8sConfigError):
        k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])


def test_build_workflow_body_raises_configerror_on_a_dispatch_label_key_collision(
    monkeypatch, tmp_path, vendored_workflow
):
    """The vendored file's own labels are merged with, never silently
    overwritten by, the four dispatch-added label keys (see the label-merge
    test above) — but if the vendored file ever defines one of THOSE SAME
    keys itself, a plain dict merge would let the dispatch value win with no
    signal that anything unusual happened. Mirrors the scan-ids
    structural-drift assertion for labels."""
    mutated = copy.deepcopy(vendored_workflow)
    mutated["metadata"].setdefault("labels", {})["environment"] = "prod"
    mutated_file = tmp_path / "mutated.yaml"
    mutated_file.write_text(yaml.safe_dump(mutated))
    monkeypatch.setattr(k8s_client, "_VENDORED_WORKFLOW_PATH", mutated_file)
    with pytest.raises(K8sConfigError):
        k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])


# --- get_workflow_status: status-polling (bloom #11 Phase 3) ----------------


def test_get_workflow_status_returns_the_phase_on_success(monkeypatch):
    resp = _FakeResp(200, {"status": {"phase": "Succeeded"}})
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: _FakeClient(resp=resp)
    )
    assert k8s_client.get_workflow_status("wf-abc") == "Succeeded"


def test_get_workflow_status_returns_none_on_404(monkeypatch):
    resp = _FakeResp(404, {}, text="not found")
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: _FakeClient(resp=resp)
    )
    assert k8s_client.get_workflow_status("wf-gone") is None


def test_get_workflow_status_raises_k8sstatuserror_on_5xx(monkeypatch):
    resp = _FakeResp(500, text="internal error")
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: _FakeClient(resp=resp)
    )
    with pytest.raises(K8sStatusError):
        k8s_client.get_workflow_status("wf-abc")


def test_get_workflow_status_raises_k8sstatuserror_on_network_error(monkeypatch):
    import httpx as real_httpx

    monkeypatch.setattr(
        k8s_client.httpx,
        "Client",
        lambda *a, **k: _FakeClient(
            raise_exc=real_httpx.ConnectError("connection refused")
        ),
    )
    with pytest.raises(K8sStatusError):
        k8s_client.get_workflow_status("wf-abc")


def test_get_workflow_status_error_message_is_generic_not_raw(monkeypatch):
    resp = _FakeResp(
        500, text="Internal Server Error at https://10.7.30.173:6443/secret-detail"
    )
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: _FakeClient(resp=resp)
    )
    with pytest.raises(K8sStatusError) as exc:
        k8s_client.get_workflow_status("wf-abc")
    message = str(exc.value)
    assert "10.7.30.173" not in message
    assert "Internal Server Error" not in message


def test_get_workflow_status_requires_config_before_any_network_call(monkeypatch):
    monkeypatch.setattr(k8s_client, "TOKEN", None)
    calls = {"requested": False}
    monkeypatch.setattr(
        k8s_client.httpx, "Client", lambda *a, **k: calls.update(requested=True)
    )
    with pytest.raises(K8sConfigError):
        k8s_client.get_workflow_status("wf-abc")
    assert calls["requested"] is False


def test_get_workflow_status_requests_the_exact_resource_path(monkeypatch):
    capture = {}
    resp = _FakeResp(200, {"status": {"phase": "Running"}})
    monkeypatch.setattr(
        k8s_client.httpx,
        "Client",
        lambda *a, **k: _FakeClient(resp=resp, capture=capture),
    )
    k8s_client.get_workflow_status("sleap-roots-pipeline-abc12")
    assert capture["url"] == (
        "https://10.7.30.173:6443/apis/argoproj.io/v1alpha1/namespaces/"
        "runai-busch-lab/workflows/sleap-roots-pipeline-abc12"
    )
    assert capture["headers"]["Authorization"] == "Bearer test-token"
