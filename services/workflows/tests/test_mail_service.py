"""The internal mail service: callers with the secret only; the connector sign-up email."""

import collections

import pytest
from fastapi.testclient import TestClient

import mail
import mail_service

SECRET = "s" * 40
AUTH = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def sent(monkeypatch):
    """The service configured, with sends recorded instead of reaching the relay."""
    monkeypatch.setattr(mail_service, "SECRET", SECRET)
    monkeypatch.setattr(mail_service, "SITE_URL", "https://bloom.salk.edu")
    monkeypatch.setattr(mail, "SMTP_HOST", "neoemex1.salk.edu")
    monkeypatch.setattr(mail_service, "_last_sent", {})
    monkeypatch.setattr(mail_service, "_recent_sends", collections.deque())
    messages = []
    monkeypatch.setattr(mail, "send", messages.append)
    return messages


@pytest.fixture
def client():
    return TestClient(mail_service.app)


def signup(client, email, headers=AUTH):
    return client.post("/connector-signup", json={"email": email}, headers=headers)


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": SECRET}],
    ids=["none", "wrong", "not bearer"],
)
def test_a_caller_without_the_secret_is_refused(client, sent, headers):
    assert signup(client, "ada@salk.edu", headers).status_code == 401
    assert sent == []


def test_an_unset_secret_refuses_everyone(client, sent, monkeypatch):
    monkeypatch.setattr(mail_service, "SECRET", "")
    assert (
        signup(client, "ada@salk.edu", {"Authorization": "Bearer "}).status_code == 401
    )
    assert sent == []


def test_the_signup_email_goes_to_the_address_with_the_signup_link(client, sent):
    resp = signup(client, " Ada@Salk.edu ")
    assert resp.status_code == 200 and resp.json() == {"sent": True}
    [msg] = sent
    assert msg["To"] == "ada@salk.edu"
    assert msg["Subject"] == mail_service.SIGNUP_SUBJECT
    body = msg.get_content()
    assert "https://bloom.salk.edu/login" in body
    assert "ada@salk.edu" in body and "Sign up" in body


@pytest.mark.parametrize(
    "email",
    ["ada@gmail.com", "ada@salk.edu.evil.com", "a@salk.edu,b@salk.edu", "@salk.edu",
     "ada @salk.edu", "x" * 250 + "@salk.edu", "", "a'b@salk.edu", 'a"b@salk.edu',
     "a\nb@salk.edu", "a/b@salk.edu", "ada@salk.edu\r\nBcc: x@evil.com"],
)  # fmt: skip
def test_only_one_salk_address_is_accepted(client, sent, email):
    assert signup(client, email).status_code == 400
    assert sent == []


def test_a_second_request_within_two_hours_sends_nothing(client, sent, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(mail_service.time, "monotonic", lambda: now[0])
    assert signup(client, "ada@salk.edu").json() == {"sent": True}
    now[0] += mail_service.RESEND_AFTER_SECONDS - 1
    assert signup(client, "ADA@salk.edu").json() == {"sent": False}
    now[0] += 2
    assert signup(client, "ada@salk.edu").json() == {"sent": True}
    assert len(sent) == 2


def test_a_relay_failure_is_502_and_does_not_count(client, sent, monkeypatch):
    def broken(msg):
        raise ConnectionRefusedError("relay down")

    monkeypatch.setattr(mail, "send", broken)
    resp = signup(client, "ada@salk.edu")
    assert resp.status_code == 502
    assert resp.json()["detail"] == "relay_error"
    monkeypatch.setattr(mail, "send", sent.append)
    assert signup(client, "ada@salk.edu").json() == {"sent": True}


@pytest.mark.parametrize("unset", ["SMTP_HOST", "SITE_URL"])
def test_without_mail_settings_nothing_is_sent(client, sent, monkeypatch, unset):
    if unset == "SMTP_HOST":
        monkeypatch.setattr(mail, "SMTP_HOST", None)
    else:
        monkeypatch.setattr(mail_service, "SITE_URL", "")
    assert signup(client, "ada@salk.edu").json() == {"sent": False}
    assert sent == []


def test_health_needs_no_secret(client):
    assert client.get("/health").status_code == 200


def test_the_service_has_no_other_routes():
    assert {r.path for r in mail_service.app.routes} == {"/connector-signup", "/health"}


def test_a_short_secret_refuses_everyone(client, sent, monkeypatch):
    short = "s" * (mail_service.MIN_SECRET_LENGTH - 1)
    monkeypatch.setattr(mail_service, "SECRET", short)
    resp = signup(client, "ada@salk.edu", {"Authorization": f"Bearer {short}"})
    assert resp.status_code == 401
    assert sent == []


def test_the_hourly_cap_stops_all_sending(client, sent, monkeypatch):
    now = [5000.0]
    monkeypatch.setattr(mail_service.time, "monotonic", lambda: now[0])
    for i in range(mail_service.MAX_SENDS_PER_HOUR):
        assert signup(client, f"user{i}@salk.edu").json() == {"sent": True}
    resp = signup(client, "one.more@salk.edu")
    assert resp.status_code == 429
    assert resp.json()["detail"] == "hourly_limit"
    assert len(sent) == mail_service.MAX_SENDS_PER_HOUR
    now[0] += 3601
    assert signup(client, "one.more@salk.edu").json() == {"sent": True}


def test_a_failed_send_does_not_count_toward_the_cap(client, sent, monkeypatch):
    def broken(msg):
        raise ConnectionRefusedError("relay down")

    monkeypatch.setattr(mail, "send", broken)
    for _ in range(mail_service.MAX_SENDS_PER_HOUR + 1):
        assert signup(client, "ada@salk.edu").status_code == 502
    monkeypatch.setattr(mail, "send", sent.append)
    assert signup(client, "ada@salk.edu").json() == {"sent": True}
