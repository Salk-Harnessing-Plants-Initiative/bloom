"""Unit tests for scripts/check_vendored_workflow_drift.py's diff logic
(bloom #737). No real network call — `fetch_canonical_file`/`fetch_with_retry`
are monkeypatched, matching this repo's other script-shape tests
(test_check_health.py) and services/workflows/tests/test_k8s_client.py's
mocked-network convention.
"""

from __future__ import annotations

import http.client
import importlib.util
import urllib.error
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
    ref.write_text("a" * 40 + "\n")
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

    def _returns_different_content(url, **kwargs):
        return b"apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nspec: {}\n"

    monkeypatch.setattr(
        check_drift_module, "fetch_with_retry", _returns_different_content
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


def test_fetch_canonical_file_raises_pinnotfounderror_on_http_404(monkeypatch):
    """A 404 means the pinned commit no longer resolves upstream (e.g. its
    branch was deleted and the commit was garbage-collected) — a permanent
    problem requiring a re-pin, not a transient one worth retrying.
    HTTPError is a subclass of URLError, so this must be special-cased
    *before* the broader except clause, or it's silently treated the same as
    a network blip."""

    def _raises_404(url, timeout=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(check_drift_module.urllib.request, "urlopen", _raises_404)
    with pytest.raises(check_drift_module.PinNotFoundError):
        check_drift_module.fetch_canonical_file("https://example.invalid/file.yaml")


def test_fetch_canonical_file_raises_generic_fetcherror_on_other_http_status(
    monkeypatch,
):
    """A non-404 HTTP error (e.g. a transient 503) must NOT be classified as
    PinNotFoundError — only 404 means the pin itself is invalid."""

    def _raises_503(url, timeout=None):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(check_drift_module.urllib.request, "urlopen", _raises_503)
    with pytest.raises(check_drift_module.FetchError) as exc_info:
        check_drift_module.fetch_canonical_file("https://example.invalid/file.yaml")
    assert not isinstance(exc_info.value, check_drift_module.PinNotFoundError)


def test_fetch_canonical_file_raises_fetcherror_on_incomplete_read(monkeypatch):
    """http.client.IncompleteRead is not a URLError/OSError subclass — left
    uncaught, it would crash the script with Python's default exit code 1,
    colliding with the script's own intentional 'content drift' exit code."""

    def _fake_urlopen(url, timeout=None):
        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def read(self):
                raise http.client.IncompleteRead(partial=b"", expected=10)

        return _FakeResponse()

    monkeypatch.setattr(check_drift_module.urllib.request, "urlopen", _fake_urlopen)
    with pytest.raises(check_drift_module.FetchError):
        check_drift_module.fetch_canonical_file("https://example.invalid/file.yaml")


def test_fetch_with_retry_recovers_from_a_transient_404_on_retry(monkeypatch):
    """A 404 right after a commit is pushed can be CDN-propagation lag, not a
    genuinely dangling pin — GitHub's raw-content CDN can briefly 404 a commit
    that resolves moments later. A single 404 must get the same retry chance
    as any other fetch failure, not be treated as immediately terminal."""
    calls = {"count": 0}

    def _404_once_then_succeeds(url, timeout=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise check_drift_module.PinNotFoundError("pinned commit not found (404)")
        return b"content"

    monkeypatch.setattr(
        check_drift_module, "fetch_canonical_file", _404_once_then_succeeds
    )
    monkeypatch.setattr(check_drift_module, "RETRY_DELAY_SECONDS", 0)

    result = check_drift_module.fetch_with_retry("https://example.invalid/file.yaml")
    assert result == b"content"
    assert calls["count"] == 2


def test_fetch_with_retry_raises_pinnotfounderror_only_after_every_attempt_404s(
    monkeypatch,
):
    """Only a 404 that persists across the full retry budget is treated as a
    genuinely dangling pin — one that resolves on retry is transient (see
    test above); one that doesn't is real."""
    calls = {"count": 0}

    def _always_404(url, timeout=None):
        calls["count"] += 1
        raise check_drift_module.PinNotFoundError("pinned commit not found (404)")

    monkeypatch.setattr(check_drift_module, "fetch_canonical_file", _always_404)
    monkeypatch.setattr(check_drift_module, "RETRY_DELAY_SECONDS", 0)

    with pytest.raises(check_drift_module.PinNotFoundError):
        check_drift_module.fetch_with_retry("https://example.invalid/file.yaml")
    assert calls["count"] == 2  # used the full retry budget, not given up early


def test_check_drift_fails_with_a_repin_required_message_on_persistent_404(
    monkeypatch, vendored_and_ref, capsys
):
    vendored, ref = vendored_and_ref

    def _always_404(url, **kwargs):
        raise check_drift_module.PinNotFoundError("pinned commit not found (404)")

    monkeypatch.setattr(check_drift_module, "fetch_with_retry", _always_404)
    exit_code = check_drift_module.check_drift(vendored, ref)
    assert exit_code == check_drift_module.EXIT_PIN_NOT_FOUND
    message = capsys.readouterr().err
    assert "RE-PIN" in message.upper()
    assert "DRIFT" not in message.upper()


def test_check_drift_rejects_a_symlinked_vendored_file(monkeypatch, tmp_path, capsys):
    """A symlink swap on the vendored path could point a future edit
    somewhere the path-scoped git diff would never notice, permanently
    blinding drift detection from that point on."""
    real_target = tmp_path / "real-target.yaml"
    real_target.write_text("apiVersion: argoproj.io/v1alpha1\n")
    symlinked_vendored = tmp_path / "sleap-roots-pipeline.yaml"
    try:
        symlinked_vendored.symlink_to(real_target)
    except OSError as exc:
        pytest.skip(f"platform/permissions don't allow creating symlinks: {exc}")
    ref = tmp_path / "SLEAP_ROOTS_PIPELINE_REF"
    ref.write_text("a" * 40 + "\n")

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("must not attempt a network fetch for a symlinked file")

    monkeypatch.setattr(check_drift_module, "fetch_with_retry", _fail_if_called)
    exit_code = check_drift_module.check_drift(symlinked_vendored, ref)
    assert exit_code != 0
    message = capsys.readouterr().err
    assert "SYMLINK" in message.upper()


def test_check_drift_fails_cleanly_when_ref_file_is_missing(
    monkeypatch, tmp_path, capsys
):
    vendored = tmp_path / "sleap-roots-pipeline.yaml"
    vendored.write_text("apiVersion: argoproj.io/v1alpha1\n")
    missing_ref = tmp_path / "does-not-exist"

    exit_code = check_drift_module.check_drift(vendored, missing_ref)
    assert exit_code != 0
    message = capsys.readouterr().err
    assert "PIN FILE" in message.upper()


def test_check_drift_fails_cleanly_and_skips_the_network_when_ref_sha_is_malformed(
    monkeypatch, tmp_path, capsys
):
    vendored = tmp_path / "sleap-roots-pipeline.yaml"
    vendored.write_text("apiVersion: argoproj.io/v1alpha1\n")
    bad_ref = tmp_path / "SLEAP_ROOTS_PIPELINE_REF"
    bad_ref.write_text("not-a-real-sha\n")

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("must not attempt a network fetch for a malformed SHA")

    monkeypatch.setattr(check_drift_module, "fetch_with_retry", _fail_if_called)
    exit_code = check_drift_module.check_drift(vendored, bad_ref)
    assert exit_code != 0
    message = capsys.readouterr().err
    assert "MALFORMED" in message.upper() or "PIN FILE" in message.upper()


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
