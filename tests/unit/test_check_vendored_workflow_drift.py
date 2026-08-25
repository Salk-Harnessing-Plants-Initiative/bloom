"""Unit tests for scripts/check_vendored_workflow_drift.py's diff logic
(bloom #737). No real network call — `fetch_canonical_file`/`fetch_with_retry`
are monkeypatched, matching this repo's other script-shape tests
(test_check_health.py) and services/workflows/tests/test_k8s_client.py's
mocked-network convention.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_vendored_workflow_drift.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_vendored_workflow_drift", _SCRIPT
    )
    assert spec and spec.loader, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_drift_module = _load()


@pytest.fixture
def vendored_and_ref(tmp_path):
    vendored = tmp_path / "sleap-roots-pipeline.yaml"
    ref = tmp_path / "SLEAP_ROOTS_PIPELINE_REF"
    vendored.write_text("apiVersion: argoproj.io/v1alpha1\nkind: Workflow\n")
    ref.write_text("abc123\n")
    return vendored, ref


def test_check_drift_passes_when_upstream_matches_vendored_copy(
    monkeypatch, vendored_and_ref
):
    vendored, ref = vendored_and_ref
    monkeypatch.setattr(
        check_drift_module,
        "fetch_with_retry",
        lambda url, **kwargs: vendored.read_bytes(),
    )
    assert check_drift_module.check_drift(vendored, ref) == 0


def test_check_drift_fails_with_a_content_mismatch_message_on_drift(
    monkeypatch, vendored_and_ref, capsys
):
    vendored, ref = vendored_and_ref
    monkeypatch.setattr(
        check_drift_module,
        "fetch_with_retry",
        lambda url,
        **kwargs: b"apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nspec: {}\n",
    )
    exit_code = check_drift_module.check_drift(vendored, ref)
    assert exit_code != 0
    message = capsys.readouterr().err
    assert "DRIFT" in message.upper()
    assert "FETCH FAILED" not in message.upper()


def test_check_drift_fails_with_a_fetch_failure_message_when_upstream_unreachable(
    monkeypatch, vendored_and_ref, capsys
):
    vendored, ref = vendored_and_ref

    def _always_raises(url, **kwargs):
        raise check_drift_module.FetchError("connection refused")

    monkeypatch.setattr(check_drift_module, "fetch_with_retry", _always_raises)
    exit_code = check_drift_module.check_drift(vendored, ref)
    assert exit_code != 0
    message = capsys.readouterr().err
    assert "FETCH" in message.upper()
    assert "DRIFT" not in message.upper()


def test_check_drift_mismatch_and_fetch_failure_produce_different_messages(
    monkeypatch, vendored_and_ref, capsys
):
    """The two failure modes must be distinguishable without reading the
    script's source — a reviewer facing a red CI check needs to know which
    one happened."""
    vendored, ref = vendored_and_ref

    monkeypatch.setattr(
        check_drift_module,
        "fetch_with_retry",
        lambda url, **kwargs: b"different content\n",
    )
    drift_code = check_drift_module.check_drift(vendored, ref)
    drift_message = capsys.readouterr().err

    def _always_raises(url, **kwargs):
        raise check_drift_module.FetchError("timed out")

    monkeypatch.setattr(check_drift_module, "fetch_with_retry", _always_raises)
    fetch_failure_code = check_drift_module.check_drift(vendored, ref)
    fetch_failure_message = capsys.readouterr().err

    assert drift_code != fetch_failure_code
    assert drift_message != fetch_failure_message


def test_fetch_with_retry_succeeds_if_the_second_attempt_matches(
    monkeypatch, vendored_and_ref
):
    """A single transient failure, recovered by the retry, must not itself
    fail the job."""
    vendored, ref = vendored_and_ref
    attempts = {"count": 0}

    def _fails_once_then_succeeds(url, timeout=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise check_drift_module.FetchError("connection reset")
        return vendored.read_bytes()

    monkeypatch.setattr(
        check_drift_module, "fetch_canonical_file", _fails_once_then_succeeds
    )
    monkeypatch.setattr(check_drift_module, "RETRY_DELAY_SECONDS", 0)

    result = check_drift_module.fetch_with_retry("https://example.invalid/file.yaml")
    assert result == vendored.read_bytes()
    assert attempts["count"] == 2


def test_fetch_with_retry_raises_fetcherror_after_exhausting_retries(monkeypatch):
    def _always_raises(url, timeout=None):
        raise check_drift_module.FetchError("connection refused")

    monkeypatch.setattr(check_drift_module, "fetch_canonical_file", _always_raises)
    monkeypatch.setattr(check_drift_module, "RETRY_DELAY_SECONDS", 0)

    with pytest.raises(check_drift_module.FetchError):
        check_drift_module.fetch_with_retry("https://example.invalid/file.yaml")


def test_fetch_canonical_file_is_called_with_an_explicit_timeout(monkeypatch):
    """The request-level timeout design.md commits to must actually be
    exercised, not just described in prose — assert it's passed through, not
    just that the script eventually times out in practice."""
    captured = {}

    def _fake_urlopen(url, timeout=None):
        captured["url"] = url
        captured["timeout"] = timeout

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def read(self):
                return b"content"

        return _FakeResponse()

    monkeypatch.setattr(check_drift_module.urllib.request, "urlopen", _fake_urlopen)
    check_drift_module.fetch_canonical_file(
        "https://example.invalid/file.yaml", timeout=7.5
    )
    assert captured["url"] == "https://example.invalid/file.yaml"
    assert captured["timeout"] == 7.5
