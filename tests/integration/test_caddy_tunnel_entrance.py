"""Live assertion: Caddy's `:8880` tunnel entrance refuses requests without
`X-Salk-User` and forwards the rest to bloommcp, which looks the user up.

Config-shape counterpart: tests/unit/test_bloommcp_tunnel_entrance.py.

8880 is not published on the host, so requests run inside the containers. Caddy's
own answers are checked with busybox `wget` in the Caddy container, which stops at
the status line on a 4xx; bloommcp's are read with Python in its container, since
its refusals are also 403 and only the body tells them apart.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.integration

TUNNEL_URL = "http://localhost:8880"
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


def _request(container: str, url: str, *headers: str) -> int | None:
    """Status code of a GET from inside the Caddy container; None if no response came back."""
    command = ["docker", "exec", container, "wget", "-S", "-q", "-O", "/dev/null", "-T", "10"]
    for header in headers:
        command += ["--header", header]
    result = subprocess.run([*command, url], capture_output=True, text=True, timeout=30, check=False)
    # -S prints the status line on stderr; wget exits non-zero on a 4xx.
    codes = STATUS_LINE.findall(result.stderr)
    return int(codes[-1]) if codes else None


def _status(container: str, path: str, *headers: str) -> int:
    """Status code of a GET to the tunnel entrance."""
    status = _request(container, f"{TUNNEL_URL}{path}", *headers)
    assert status is not None, f"no HTTP status from the tunnel entrance for {path}"
    return status


# A path-specific route added to the site would be ordered before the refusal.
@pytest.mark.parametrize("path", ["/", "/health", "/mcp", "/mcp/x"])
def test_request_without_salk_user_is_refused_by_caddy(caddy_container, path):
    assert _status(caddy_container, path) == 403


# Sent from bloommcp's container with Python, which reads the body of an error response;
# busybox wget cannot, and both Caddy and bloommcp answer 403.
_PY_GET = """
import sys, urllib.request, urllib.error
url, headers = sys.argv[1], dict(h.split(": ", 1) for h in sys.argv[2:])
try:
    response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10)
    print(response.status); print(response.read(2000).decode(errors="replace"))
except urllib.error.HTTPError as error:
    print(error.code); print(error.read(2000).decode(errors="replace"))
"""
NO_ACCOUNT = "nobody-ci@salk.edu"


@pytest.fixture(scope="module")
def bloommcp_container() -> str:
    result = subprocess.run(
        ["docker", "ps", "-q", "--filter", "label=com.docker.compose.service=bloommcp"],
        capture_output=True,
        text=True,
        check=False,
    )
    ids = result.stdout.split()
    if len(ids) != 1:
        _unavailable(f"expected one running bloommcp container, found {len(ids)}")
    return ids[0]


def _from_bloommcp(container: str, url: str, *headers: str) -> tuple[int, str]:
    result = subprocess.run(
        ["docker", "exec", container, "python", "-c", _PY_GET, url, *headers],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    status, _, body = result.stdout.partition("\n")
    assert status.strip().isdigit(), f"no HTTP status: {result.stdout!r} {result.stderr!r}"
    return int(status), body


def test_a_salk_user_without_an_account_gets_bloommcps_refusal(bloommcp_container):
    status, body = _from_bloommcp(
        bloommcp_container, "http://caddy:8880/mcp", f"X-Salk-User: {NO_ACCOUNT}"
    )
    assert status == 403
    assert f"No Bloom account for {NO_ACCOUNT}" in body, body


def test_a_forged_tunnel_header_through_caddy_is_replaced(bloommcp_container):
    status, body = _from_bloommcp(
        bloommcp_container,
        "http://caddy:8880/mcp",
        f"X-Salk-User: {NO_ACCOUNT}",
        "X-Bloom-Tunnel: forged",
    )
    assert status == 403
    assert f"No Bloom account for {NO_ACCOUNT}" in body, body


def test_a_forged_tunnel_header_straight_to_bloommcp_is_ignored(bloommcp_container):
    forged = _from_bloommcp(
        bloommcp_container,
        "http://bloommcp:8811/mcp",
        f"X-Salk-User: {NO_ACCOUNT}",
        "X-Bloom-Tunnel: forged",
    )
    plain = _from_bloommcp(bloommcp_container, "http://bloommcp:8811/mcp")
    assert forged[0] == plain[0] == 401
    assert "No Bloom account" not in forged[1]


@pytest.fixture(scope="module")
def caddy_tunnel_ip(caddy_container) -> str:
    """Caddy's address on the tunnel network; a request to it from inside Caddy comes from it."""
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range $name, $net := .NetworkSettings.Networks}}{{$name}} {{$net.IPAddress}}\n{{end}}",
            caddy_container,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.splitlines():
        name, _, ip = line.partition(" ")
        if name.endswith("_tunnel") and ip:
            return ip
    _unavailable(f"caddy is not on a tunnel network: {result.stdout!r}")


def test_tunnel_traffic_still_reaches_the_entrance(caddy_container, caddy_tunnel_ip):
    assert _request(caddy_container, f"http://{caddy_tunnel_ip}:8880/mcp") == 403


# localhost and studio.localhost are main-site addresses in CI; a bare IP lands on the :80 catch-all.
@pytest.mark.parametrize(
    "port, path, headers",
    [
        (80, "/", ()),
        (80, "/", ("Host: localhost",)),
        (80, "/", ("Host: studio.localhost",)),
        (2021, "/caddy-health", ()),
    ],
)
def test_tunnel_traffic_is_dropped_on_every_other_site(caddy_container, caddy_tunnel_ip, port, path, headers):
    # The same request from loopback is answered, so a missing answer is the drop, not a dead port.
    assert _request(caddy_container, f"http://localhost:{port}{path}", *headers) is not None
    assert _request(caddy_container, f"http://{caddy_tunnel_ip}:{port}{path}", *headers) is None
