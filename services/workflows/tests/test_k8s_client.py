"""Unit tests for k8s_client.py — credential validation, Workflow CRD
construction, and the raw K8s REST submission call. Mocks `httpx` the same
way test_auth.py already does for JWT validation (a monkeypatched
`_FakeClient`/`_FakeResp` onto `k8s_client.httpx.Client`) — no real network
call, no real Kubernetes cluster."""

import datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import k8s_client
from k8s_client import K8sConfigError, K8sStatusError, K8sSubmissionError


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


def test_build_workflow_body_dag_references_all_four_templates_in_order():
    body = k8s_client.build_workflow_body(run_id=1, batch_index=0, scan_ids=[1])
    tasks = body["spec"]["templates"][0]["dag"]["tasks"]
    names_in_order = [t["templateRef"]["name"] for t in tasks]
    assert names_in_order == [
        "sleap-roots-images-downloader-template",
        "sleap-roots-predictor-template",
        "sleap-roots-trait-extractor-template",
        "sleap-roots-write-back-template",
    ]
    # dependencies chain matches the order — each task depends on the previous
    deps = [t.get("dependencies") for t in tasks]
    assert deps[0] is None or deps[0] == []
    assert deps[1] == [tasks[0]["name"]]
    assert deps[2] == [tasks[1]["name"]]
    assert deps[3] == [tasks[2]["name"]]


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
