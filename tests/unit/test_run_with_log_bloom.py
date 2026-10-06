"""Tests for how argo/scrna/run_with_log.py talks to Bloom: signing in only over https,
refusing redirects, signing in again when a token is refused, retrying the last upload, never
printing a secret, and adding a retried step's log to the earlier attempts'. The wrapper runs
as a real subprocess against a fake Bloom on localhost."""

import pytest

from tests.unit import _fake_bloom as fb


@pytest.fixture
def bloom():
    fake = fb.FakeBloom()
    yield fake
    fake.close()


@pytest.fixture
def credentials(tmp_path, bloom):
    return fb.write_credentials(tmp_path, bloom.url)


def test_a_failed_upload_never_fails_the_step(bloom, credentials):
    bloom.upload_status = 500
    result = fb.run(fb.py("print('fine')"), credentials, timeout=90)
    assert result.returncode == 0
    assert "couldn't upload the log (HTTP 500)" in result.stderr
    assert "the end of the log wasn't uploaded" in result.stderr


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_token_signs_in_again(bloom, credentials, status):
    bloom.reject_next_upload = True
    bloom.reject_status = status
    result = fb.run(fb.py("print('fine')"), credentials)
    assert result.returncode == 0
    assert bloom.sign_ins == 2
    assert bloom.uploads[-1]["body"] == "fine\n"
    assert bloom.uploads[-1]["headers"]["authorization"] == f"Bearer {fb.TOKEN}-2", (
        "the retried upload didn't use the new token"
    )


def test_the_last_upload_is_tried_again_until_it_works(bloom, credentials):
    # A long interval leaves only the last upload, which fails twice then works.
    bloom.fail_next_uploads = 2
    result = fb.run(
        fb.py("print('aligning'); print('counted')"), credentials, interval="60"
    )
    assert result.returncode == 0
    assert bloom.uploads[-1]["body"] == "aligning\ncounted\n"
    assert result.stderr.count("couldn't upload the log") == 1, (
        "the failure is noted once"
    )
    assert "uploads are working again" in result.stderr
    assert "the end of the log wasn't uploaded" not in result.stderr


def test_without_credentials_the_step_runs_and_nothing_is_uploaded(tmp_path, bloom):
    result = fb.run(
        fb.py("import sys; print('fine'); sys.exit(3)"), tmp_path / "missing.txt"
    )
    assert result.returncode == 3
    assert result.stdout == "fine\n"
    assert "no Bloom credentials" in result.stderr
    assert bloom.sign_ins == 0 and bloom.uploads == []


def test_a_bloom_url_that_isnt_https_is_never_signed_in_to(tmp_path, bloom):
    path = tmp_path / "credentials.txt"
    path.write_text(
        "BLOOM_API_URL=http://bloom.example.org/api\n"
        "BLOOM_ANON_KEY=fake-anon-key\n"
        "BLOOM_EMAIL=pipeline@bloom.test\n"
        f"BLOOM_PASSWORD={fb.PASSWORD}\n"
    )
    result = fb.run(fb.py("import sys; print('fine'); sys.exit(3)"), path)
    assert result.returncode == 3
    assert "BLOOM_API_URL isn't https" in result.stderr


@pytest.mark.parametrize(
    "url, safe",
    [
        ("https://bloom.salk.edu/api", True),
        ("http://bloom.salk.edu/api", False),
        ("http://127.0.0.1:8000", True),
        ("http://localhost:8000", True),
        ("ftp://bloom.salk.edu", False),
        ("https://", False),
    ],
)
def test_only_https_or_this_machine_is_signed_in_to(url, safe):
    assert fb.wrapper_module().is_safe_api_url(url) is safe


def test_a_redirect_is_not_followed_with_the_token(bloom, credentials):
    bloom.redirect_uploads = True
    result = fb.run(fb.py("print('fine')"), credentials, timeout=90)
    assert result.returncode == 0
    assert bloom.redirected == [], "the upload followed a redirect"
    assert "HTTP 302" in result.stderr


def test_the_token_and_password_are_never_printed(bloom, credentials):
    bloom.reject_next_upload = True
    result = fb.run(fb.py("print('fine')"), credentials)
    for text in (result.stdout, result.stderr, *(u["body"] for u in bloom.uploads)):
        assert fb.TOKEN not in text and fb.PASSWORD not in text


def test_a_retried_step_adds_to_the_earlier_attempts_log(bloom, credentials):
    first = fb.run(
        fb.py("import sys; print('out of memory'); sys.exit(137)"), credentials
    )
    assert first.returncode == 137
    second = fb.run(fb.py("print('counted')"), credentials)
    assert second.returncode == 0
    assert second.stdout == "counted\n", (
        "the earlier attempt must not reach the pod's log"
    )
    body = bloom.uploads[-1]["body"]
    assert body.startswith("out of memory\n--- run-with-log: retried at ")
    assert body.endswith("; earlier attempts above ---\ncounted\n")


@pytest.mark.parametrize(
    "missing_as_400", [False, True], ids=["404", "older-storage-400"]
)
def test_a_first_attempt_starts_a_new_log(bloom, credentials, missing_as_400):
    bloom.missing_as_400 = missing_as_400
    result = fb.run(fb.py("print('fine')"), credentials)
    assert bloom.uploads[-1]["body"] == "fine\n"
    assert "earlier attempts" not in result.stderr


def test_an_unreadable_earlier_log_starts_a_new_one_and_says_so(bloom, credentials):
    bloom.stored[fb.LOG_PATH] = "lost\n"
    bloom.download_status = 500
    result = fb.run(fb.py("print('fine')"), credentials)
    assert result.returncode == 0
    assert "couldn't read earlier attempts' log (HTTP 500)" in result.stderr
    assert bloom.uploads[-1]["body"] == "fine\n"
