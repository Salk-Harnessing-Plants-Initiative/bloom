"""signup_email: asks Bloom's internal mail service to email a refused Salk user, never waiting."""

from __future__ import annotations

import logging
import threading

import httpx
import pytest

from bloom_mcp import signup_email

URL = "http://mail:5200"
SECRET = "m" * 40


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
    release = threading.Event()
    started = threading.Event()

    def slow_post(url, **kwargs):
        started.set()
        release.wait(5)
        return httpx.Response(
            200, json={"sent": True}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(signup_email.httpx, "post", slow_post)
    signup_email.request("ada@salk.edu")
    assert started.wait(2)
    release.set()


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
