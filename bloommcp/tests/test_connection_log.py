"""Rejected MCP requests are logged with their reason; other responses are not."""

from __future__ import annotations

import json
import logging

import pytest
from starlette.testclient import TestClient

from bloom_mcp import connection_log as cl
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
    assert lines == [f"mcp request rejected: {status} {reason} (not via the connector)"]


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
    ],
)
def test_reason_reads_the_error_message(body, expected):
    assert cl.reason(body) == expected


def test_reason_is_capped():
    assert len(cl.reason(b"x" * 5000)) == cl.MAX_REASON_CHARS


def test_the_logger_prints_itself_at_info():
    assert cl.connection_log.propagate is False
    assert cl.connection_log.isEnabledFor(logging.INFO)
