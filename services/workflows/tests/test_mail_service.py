"""The internal mail service: callers with the secret only; the connector sign-up email."""

import collections
import logging
import logging.handlers
import threading

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
    monkeypatch.setattr(mail_service, "CONTACT", "bfernando@salk.edu")
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
    assert body.startswith("You tried to use Bloom in Claude as ada@salk.edu")
    assert "couldn't find an active account" in body
    assert "https://bloom.salk.edu/login" in body and "Sign up" in body
    assert "not your Salk password" in body
    assert "Someone" not in body


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


def test_a_slow_send_holds_up_only_its_own_email(sent, monkeypatch):
    """The lock covers only the checks: others send, and /health answers, meanwhile."""
    release = threading.Event()
    sending = threading.Event()

    def send(msg):
        if msg["To"] == "slow@salk.edu":
            sending.set()
            assert release.wait(5)
        sent.append(msg)

    monkeypatch.setattr(mail, "send", send)
    results = {}
    slow = threading.Thread(
        target=lambda: results.update(
            slow=signup(TestClient(mail_service.app), "slow@salk.edu").json()
        )
    )
    slow.start()
    assert sending.wait(2)
    other = TestClient(mail_service.app)
    assert signup(other, "fast@salk.edu").json() == {"sent": True}
    assert signup(other, "slow@salk.edu").json() == {"sent": False}
    assert other.get("/health").status_code == 200
    release.set()
    slow.join(5)
    assert results["slow"] == {"sent": True}
    assert sorted(m["To"] for m in sent) == ["fast@salk.edu", "slow@salk.edu"]


def test_the_log_file_records_each_outcome(client, sent, monkeypatch, tmp_path):
    monkeypatch.setattr(mail_service.logger, "handlers", [])
    mail_service.setup_logging(str(tmp_path))
    signup(client, "ada@salk.edu")
    signup(client, "ada@salk.edu")

    def broken(msg):
        raise ConnectionRefusedError("relay down")

    monkeypatch.setattr(mail, "send", broken)
    signup(client, "ben@salk.edu")
    for handler in mail_service.logger.handlers:
        handler.flush()
    log = (tmp_path / "mail.log").read_text()
    assert "sent sign-up email to ada@salk.edu" in log
    assert "skipped ada@salk.edu" in log
    assert "failed ben@salk.edu: relay error ConnectionRefusedError" in log
    signup(client, "ada@gmail.com")
    signup(client, "ada@salk.edu", {"Authorization": "Bearer wrong"})
    for handler in mail_service.logger.handlers:
        handler.flush()
    log = (tmp_path / "mail.log").read_text()
    assert "refused a request for an address that isn't one Salk address" in log
    assert "refused a caller without the right secret" not in log
    assert "ada@gmail.com" not in log
    assert SECRET not in log


def test_an_unwritable_log_dir_still_starts(monkeypatch, tmp_path):
    monkeypatch.setattr(mail_service.logger, "handlers", [])
    mail_service.setup_logging(str(tmp_path / "missing"))
    assert all(
        not isinstance(h, logging.handlers.RotatingFileHandler)
        for h in mail_service.logger.handlers
    )


@pytest.mark.parametrize(
    "body",
    [None, b"not json", b'{"wrong": 1}', b"x" * 5000],
    ids=["no body", "not json", "wrong shape", "huge"],
)
def test_without_the_secret_the_body_is_never_read(client, sent, body):
    """401 whatever the body: without the guard FastAPI would parse it first and answer 422."""
    kwargs = {"content": body} if body is not None else {}
    resp = client.post(
        "/connector-signup", headers={"Content-Type": "application/json"}, **kwargs
    )
    assert resp.status_code == 401
    assert resp.json() == {"detail": "unauthorized"}
    assert sent == []


def test_a_body_over_the_limit_is_refused_before_it_is_read(client, sent):
    big = b'{"email": "' + b"a" * mail_service.MAX_BODY_BYTES + b'@salk.edu"}'
    resp = client.post(
        "/connector-signup",
        content=big,
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert sent == []


def test_a_body_without_a_declared_length_is_refused(client, sent):
    def chunks():
        yield b'{"email": "ada@salk.edu"}'

    resp = client.post(
        "/connector-signup",
        content=chunks(),
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert resp.status_code == 411
    assert sent == []


def test_a_body_longer_than_declared_is_cut_off(sent):
    """A caller that lies about its length still can't make the service read past the limit."""
    received = []

    async def inner(scope, receive, send):
        while True:
            message = await receive()
            received.append(message)
            if message["type"] == "http.disconnect" or not message.get("more_body"):
                break

    import asyncio

    guard = mail_service.GuardBeforeBody(inner)
    chunk = b"x" * 600
    messages = iter(
        [
            {"type": "http.request", "body": chunk, "more_body": True},
            {"type": "http.request", "body": chunk, "more_body": True},
            {"type": "http.request", "body": chunk, "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    async def send(message):
        pass

    scope = {
        "type": "http",
        "path": "/connector-signup",
        "method": "POST",
        "headers": [
            (b"authorization", f"Bearer {SECRET}".encode()),
            (b"content-length", b"10"),
        ],
    }
    asyncio.run(guard(scope, receive, send))
    assert received[-1] == {"type": "http.disconnect"}
    assert sum(len(m.get("body", b"")) for m in received) <= 600


def test_with_the_secret_a_normal_request_still_sends(client, sent):
    assert signup(client, "ada@salk.edu").json() == {"sent": True}


def test_the_contact_is_named_and_gets_the_replies(client, sent):
    signup(client, "ada@salk.edu")
    [msg] = sent
    assert msg["Reply-To"] == "bfernando@salk.edu"
    assert "contact the Bloom team at bfernando@salk.edu, or reply" in msg.get_content()


def test_without_a_contact_there_is_no_reply_to(client, sent, monkeypatch):
    monkeypatch.setattr(mail_service, "CONTACT", "")
    signup(client, "ada@salk.edu")
    [msg] = sent
    assert msg["Reply-To"] is None
    assert "contact the Bloom team.\n" in msg.get_content()
    assert "reply to this email" not in msg.get_content()


def test_unauthenticated_callers_are_logged_to_stdout_only(tmp_path, monkeypatch):
    """A flood without the secret can't rotate the record of sends out of mail.log."""
    monkeypatch.setattr(mail_service.logger, "handlers", [])
    monkeypatch.setattr(mail_service.unauthenticated, "handlers", [])
    mail_service.setup_logging(str(tmp_path))
    kinds = [type(h) for h in mail_service.unauthenticated.handlers]
    assert kinds == [logging.StreamHandler]
    assert mail_service.unauthenticated.propagate is False
    for handler in mail_service.logger.handlers:
        handler.close()
