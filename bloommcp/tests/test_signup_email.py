"""signup_email: asks Bloom's internal mail service to email a refused Salk user, never waiting."""

from __future__ import annotations

import logging
import threading
import time

import httpx
import pytest

from bloom_mcp import signup_email

URL = "http://mail:5200"
SECRET = "m" * 40


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """Each test starts with no remembered addresses and nothing in flight."""
    monkeypatch.setattr(signup_email, "_asked", {})
    yield
    assert signup_email._threads_idle(5)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv(signup_email.URL_ENV, URL)
    monkeypatch.setenv(signup_email.SECRET_ENV, SECRET)


@pytest.fixture
def posts(monkeypatch):
    """Records each post the client would make and waits for the background thread."""
    calls = []
    done = threading.Event()

    def fake_post(url, *, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        done.set()
        return httpx.Response(
            200, json={"sent": True}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(signup_email.httpx, "post", fake_post)
    return calls, done


def test_a_request_posts_the_address_with_the_secret(configured, posts):
    calls, done = posts
    signup_email.request("ada@salk.edu")
    assert done.wait(2)
    [(url, body, headers, timeout)] = calls
    assert url == f"{URL}/connector-signup"
    assert body == {"email": "ada@salk.edu"}
    assert headers == {"Authorization": f"Bearer {SECRET}"}
    assert timeout == signup_email.TIMEOUT_SECONDS


def test_request_returns_before_the_post_finishes(configured, monkeypatch):
    """Fails if the post ran in the caller's thread: request() must return while it blocks."""
    release = threading.Event()
    started = threading.Event()

    def slow_post(url, **kwargs):
        started.set()
        assert release.wait(5)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(signup_email.httpx, "post", slow_post)
    began = time.monotonic()
    signup_email.request("ada@salk.edu")
    returned_after = time.monotonic() - began
    assert not release.is_set()
    assert returned_after < 0.5
    assert started.wait(2)
    release.set()
    assert signup_email._threads_idle(2)


@pytest.mark.parametrize("unset", [signup_email.URL_ENV, signup_email.SECRET_ENV])
def test_without_url_or_secret_nothing_is_posted(configured, posts, monkeypatch, unset):
    monkeypatch.delenv(unset)
    calls, done = posts
    signup_email.request("ada@salk.edu")
    assert not done.wait(0.2)
    assert calls == []


def test_an_unreachable_service_is_logged_not_raised(configured, monkeypatch, caplog):
    def broken(url, **kwargs):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(signup_email.httpx, "post", broken)
    with caplog.at_level(logging.WARNING, logger=signup_email.logger.name):
        signup_email.request("ada@salk.edu")
        assert signup_email._threads_idle(2)
    assert "could not reach the mail service: ConnectError" in caplog.text
    assert SECRET not in caplog.text


def test_the_answer_and_a_timeout_are_not_logged(configured, monkeypatch, caplog):
    answers = iter(
        [
            lambda url: httpx.Response(502, request=httpx.Request("POST", url)),
            lambda url: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
        ]
    )
    monkeypatch.setattr(
        signup_email.httpx, "post", lambda url, **kw: next(answers)(url)
    )
    with caplog.at_level(logging.WARNING, logger=signup_email.logger.name):
        signup_email.request("ada@salk.edu")
        assert signup_email._threads_idle(2)
        signup_email.request("ada@salk.edu")
        assert signup_email._threads_idle(2)
    assert caplog.text == ""


def test_the_same_address_is_asked_about_once_per_window(
    configured, posts, monkeypatch
):
    calls, _ = posts
    now = [100.0]
    monkeypatch.setattr(signup_email.time, "monotonic", lambda: now[0])
    signup_email.request("ada@salk.edu")
    assert signup_email._threads_idle(2)
    signup_email.request("ada@salk.edu")
    signup_email.request("ben@salk.edu")
    assert signup_email._threads_idle(2)
    assert [c[1]["email"] for c in calls] == ["ada@salk.edu", "ben@salk.edu"]
    now[0] += signup_email.ASK_AGAIN_AFTER_SECONDS
    signup_email.request("ada@salk.edu")
    assert signup_email._threads_idle(2)
    assert len(calls) == 3


def test_at_most_two_requests_are_in_flight(configured, monkeypatch):
    release = threading.Event()
    calls = []

    def slow_post(url, *, json, **kwargs):
        calls.append(json["email"])
        release.wait(5)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(signup_email.httpx, "post", slow_post)
    for name in ("a", "b", "c"):
        signup_email.request(f"{name}@salk.edu")
    release.set()
    assert signup_email._threads_idle(5)
    assert sorted(calls) == ["a@salk.edu", "b@salk.edu"]
    # The skipped address wasn't remembered, so its next try asks.
    signup_email.request("c@salk.edu")
    assert signup_email._threads_idle(5)
    assert "c@salk.edu" in calls


def test_an_unreachable_service_lets_the_next_try_ask_again(configured, monkeypatch):
    attempts = []

    def broken(url, *, json, **kwargs):
        attempts.append(json["email"])
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(signup_email.httpx, "post", broken)
    signup_email.request("ada@salk.edu")
    assert signup_email._threads_idle(2)
    signup_email.request("ada@salk.edu")
    assert signup_email._threads_idle(2)
    assert attempts == ["ada@salk.edu", "ada@salk.edu"]


def test_a_thread_that_cant_start_never_raises(configured, monkeypatch, caplog):
    class NoThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(signup_email.threading, "Thread", NoThread)
    with caplog.at_level(logging.WARNING, logger=signup_email.logger.name):
        signup_email.request("ada@salk.edu")
    assert "could not start the sign-up email request: RuntimeError" in caplog.text
    assert signup_email._active == 0
    assert "ada@salk.edu" not in signup_email._asked
