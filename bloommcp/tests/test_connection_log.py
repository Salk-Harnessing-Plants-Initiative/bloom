"""Rejected MCP requests are logged with their reason; other responses are not."""

from __future__ import annotations

import json
import logging

import pytest
from starlette.testclient import TestClient

from bloom_mcp import connection_log as cl
from bloom_mcp import tunnel
from bloom_mcp.identity import MAX_ERROR_BODY_BYTES, IdentityMiddleware

MISSING_SESSION = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": "server-error",
        "error": {"code": -32600, "message": "Bad Request: Missing session ID"},
    }
).encode()


class _Lines(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def lines():
    handler = _Lines()
    cl.connection_log.addHandler(handler)
    yield handler.lines
    cl.connection_log.removeHandler(handler)


@pytest.fixture(autouse=True)
def _no_usage_recording(monkeypatch):
    from bloom_mcp import usage

    monkeypatch.setattr(usage, "record_usage_async", lambda i, a: None)


EMAIL = "jdoe@salk.edu"


@pytest.fixture(autouse=True)
def connector(monkeypatch):
    """Every request arrives through the Salk connector as EMAIL, unless a test says not."""

    async def salk_user(headers):
        return EMAIL

    async def bloom_user(email):
        return "00000000-0000-0000-0000-000000000001"

    monkeypatch.setattr(tunnel, "is_tunnel_request", lambda headers: True)
    monkeypatch.setattr(tunnel, "api_key", lambda: "test-api-key")
    monkeypatch.setattr(tunnel, "salk_user", salk_user)
    monkeypatch.setattr(tunnel, "bloom_user", bloom_user)


class _App:
    """Responds with ``status`` and ``chunks`` as the body, in that many messages."""

    def __init__(self, status: int, *chunks: bytes):
        self.status = status
        self.chunks = chunks or (b"",)

    async def __call__(self, scope, receive, send):
        await send(
            {"type": "http.response.start", "status": self.status, "headers": []}
        )
        for i, chunk in enumerate(self.chunks):
            more = i < len(self.chunks) - 1
            await send({"type": "http.response.body", "body": chunk, "more_body": more})


def _post(app, path="/mcp"):
    return TestClient(IdentityMiddleware(app)).post(path, content=b"{}")


@pytest.mark.parametrize("status", [400, 404, 406])
def test_a_rejected_request_logs_its_status_and_reason(lines, status):
    resp = _post(_App(status, MISSING_SESSION))
    assert resp.status_code == status
    reason = "Bad Request: Missing session ID"
    assert lines == [f"mcp request rejected: {status} {reason} ({EMAIL})"]


def test_a_request_not_via_the_connector_is_not_logged(lines, monkeypatch):
    monkeypatch.setattr(tunnel, "is_tunnel_request", lambda headers: False)
    resp = _post(_App(404, MISSING_SESSION), path="/nope")
    assert resp.status_code == 404
    assert lines == []


@pytest.mark.parametrize("status", [200, 202, 401, 500])
def test_other_responses_are_not_logged(lines, status):
    _post(_App(status, MISSING_SESSION))
    assert lines == []


def test_health_is_not_logged(lines):
    _post(_App(404, b"nope"), path="/health")
    assert lines == []


def test_a_reason_split_across_messages_is_joined(lines):
    _post(_App(400, MISSING_SESSION[:30], MISSING_SESSION[30:]))
    assert "Bad Request: Missing session ID" in lines[0]


def test_only_the_start_of_a_long_body_is_kept(lines, monkeypatch):
    seen = []
    monkeypatch.setattr(cl, "reason", lambda body: seen.append(body) or "x")
    _post(_App(400, b"a" * MAX_ERROR_BODY_BYTES, b"b" * 10))
    assert seen == [b"a" * MAX_ERROR_BODY_BYTES]


@pytest.mark.parametrize(
    "body,expected",
    [
        (MISSING_SESSION, "Bad Request: Missing session ID"),
        (
            b'{"error": "Not Acceptable: Client must accept text/event-stream"}',
            "Not Acceptable: Client must accept text/event-stream",
        ),
        (b"Not Found", "Not Found"),
        (b"two\n  lines", "two lines"),
        (b"", "(no body)"),
        (b'{"detail": "x"}', '{"detail": "x"}'),
        (b"\xff\xfe", "\ufffd\ufffd"),
        (b'{"error": {"code": -32600}}', '{"error": {"code": -32600}}'),
        (b'{"error": 404}', '{"error": 404}'),
        (b"esc\x1b[31m bel\x07 nul\x00 del\x7f", "esc [31m bel nul del"),
    ],
)
def test_reason_reads_the_error_message(body, expected):
    assert cl.reason(body) == expected


def test_reason_is_capped():
    assert len(cl.reason(b"x" * 5000)) == cl.MAX_REASON_CHARS


def test_the_logger_prints_itself_at_info():
    assert cl.connection_log.propagate is False
    assert cl.connection_log.isEnabledFor(logging.INFO)


VALIDATION_ERROR = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": "server-error",
        "error": {
            "code": -32602,
            "message": "Validation error: 4 validation errors for JSONRPCMessage\n"
            + "params\n  Input should be a valid dictionary " * 40,
        },
    }
).encode()


def test_a_body_cut_mid_json_still_logs_the_message(lines):
    assert len(VALIDATION_ERROR) > MAX_ERROR_BODY_BYTES
    _post(_App(400, VALIDATION_ERROR))
    assert lines[0].startswith(
        "mcp request rejected: 400 Validation error: 4 validation errors for "
        "JSONRPCMessage params Input should be"
    )


@pytest.mark.parametrize(
    "body,expected",
    [
        (b'{"error": {"message": "Bad \\"quoted\\" input', 'Bad "quoted" input'),
        (b'{"error": {"message": "cut in an escape \\u00', "cut in an escape \\u00"),
        (b'{"error": {"code": -1, "mess', '{"error": {"code": -1, "mess'),
    ],
)
def test_reason_reads_a_cut_message(body, expected):
    assert cl.reason(body) == expected


def test_the_real_sdk_rejection_logs_its_message(lines, monkeypatch):
    # The MCP SDK's own 400, so an SDK change to its error body fails here.
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    from bloom_mcp import server

    with TestClient(server.build_app()) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
    assert resp.status_code == 400
    assert lines == [
        f"mcp request rejected: 400 Bad Request: Missing session ID ({EMAIL})"
    ]
