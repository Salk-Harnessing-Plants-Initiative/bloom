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


def test_a_failed_post_is_logged_not_raised(configured, monkeypatch, caplog):
    done = threading.Event()

    def broken(url, **kwargs):
        done.set()
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(signup_email.httpx, "post", broken)
    with caplog.at_level(logging.WARNING, logger=signup_email.logger.name):
        signup_email.request("ada@salk.edu")
        assert done.wait(2)
        signup_email._threads_idle(2)
    assert "ada@salk.edu" in caplog.text and "ConnectError" in caplog.text
    assert SECRET not in caplog.text


def test_an_error_status_is_logged_with_its_code(configured, monkeypatch, caplog):
    def refused(url, **kwargs):
        return httpx.Response(
            502, json={"detail": "relay_error"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(signup_email.httpx, "post", refused)
    with caplog.at_level(logging.WARNING, logger=signup_email.logger.name):
        signup_email.request("ada@salk.edu")
        signup_email._threads_idle(2)
    assert "502" in caplog.text
