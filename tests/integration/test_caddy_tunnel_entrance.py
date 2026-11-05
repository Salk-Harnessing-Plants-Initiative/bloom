"""Live assertion: Caddy's `:8880` tunnel entrance refuses requests without
`X-Salk-User` and forwards the rest to bloommcp.

Config-shape counterpart: tests/unit/test_bloommcp_tunnel_entrance.py.

8880 is not published on the host, so each request runs inside the Caddy
container with its busybox `wget`. Busybox stops at the status line on a 4xx
(no headers, no body), so the status code is the discriminator: the `:8880`
site answers 403 by itself and never 401, so a 401 can only come from
bloommcp refusing the missing bearer token.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.integration

TUNNEL_URL = "http://localhost:8880/mcp"
STATUS_LINE = re.compile(r"HTTP/\d(?:\.\d)?\s+(\d{3})")
IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def _unavailable(reason: str):
    """Fail in CI, where the stack is always up; skip on a laptop without it."""
    if IN_CI:
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def caddy_container() -> str:
    if shutil.which("docker") is None:
        _unavailable("docker is not installed")
    result = subprocess.run(
        ["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=caddy"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        _unavailable(f"docker is not reachable: {result.stderr.strip()}")
    ids = result.stdout.split()
    if len(ids) != 1:
        _unavailable(f"expected one running caddy container, found {len(ids)}")
    return ids[0]


def _status(container: str, *headers: str) -> int:
    """Status code of a GET to the tunnel entrance from inside the Caddy container."""
    command = ["docker", "exec", container, "wget", "-S", "-q", "-O", "/dev/null", "-T", "10"]
    for header in headers:
        command += ["--header", header]
    result = subprocess.run(
        [*command, TUNNEL_URL], capture_output=True, text=True, timeout=30, check=False
    )
    # -S prints the status line on stderr; wget exits non-zero on a 4xx.
    codes = STATUS_LINE.findall(result.stderr)
    assert codes, f"no HTTP status from wget (exit {result.returncode}): {result.stderr}"
    return int(codes[-1])


def test_request_without_salk_user_is_refused_by_caddy(caddy_container):
    assert _status(caddy_container) == 403


def test_request_with_salk_user_reaches_bloommcp(caddy_container):
    status = _status(caddy_container, "X-Salk-User: ci@salk.edu")
    assert status == 401, (
        f"expected bloommcp's 401 for a missing bearer token, got {status}; "
        "403 means Caddy refused the header, 502 means bloommcp was not reached"
    )
