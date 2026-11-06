"""Live assertion: Caddy's `:8880` tunnel entrance refuses requests without
AIRC's bearer token and forwards the rest to bloommcp, which verifies the token
and looks the user up. CI signs tokens with a throwaway key
(scripts/generate_ci_tunnel_keys.py) whose public half bloommcp reads.

Config-shape counterpart: tests/unit/test_bloommcp_tunnel_entrance.py.

8880 is not published on the host and accepts only cloudflared's fixed address.
cloudflared does not run in CI, so requests are sent with Python from a throwaway
container on the tunnel network at that address; both Caddy and bloommcp refuse
with 403, and only the body tells them apart. Drops are checked with busybox
`wget` from Caddy's own tunnel-network address, and from the host.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

TUNNEL_PORT = 8880
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


# A path-specific route added to the site would be ordered before the refusal.
@pytest.mark.parametrize("path", ["/", "/health", "/mcp", "/mcp/x"])
def test_request_without_a_bearer_token_is_refused_by_caddy(tunnel_client, path):
    status, body = _python_get(tunnel_client, f"http://caddy:{TUNNEL_PORT}{path}")
    assert (status, body.strip()) == (403, "forbidden")


# Sent with Python, which reads the body of an error response; busybox wget cannot, and
# both Caddy and bloommcp answer 403. With no response, prints an empty status line and the error.
_PY_GET = """
import os, sys, urllib.request, urllib.error
url, headers = sys.argv[1], dict(h.split(": ", 1) for h in sys.argv[2:])
data = os.environ.get("POST_BODY", "").encode() or None
try:
    request = urllib.request.Request(url, data=data, headers=headers)
    response = urllib.request.urlopen(request, timeout=10)
    print(response.status); print(response.read(2000).decode(errors="replace"))
except urllib.error.HTTPError as error:
    print(error.code); print(error.read(2000).decode(errors="replace"))
except OSError as error:
    print(); print(f"{type(error).__name__}: {error}")
"""
NO_ACCOUNT = "nobody-ci@salk.edu"
CI_TUNNEL_KEY = Path(__file__).resolve().parents[2] / ".ci-tunnel" / "key.pem"


def _bearer(email: str, **overrides) -> str:
    """An Authorization header with a token shaped like AIRC's, signed with CI's key."""
    import jwt

    if not CI_TUNNEL_KEY.exists():
        _unavailable(f"no CI tunnel key at {CI_TUNNEL_KEY}")
    claims = {
        "iss": "bloom-mcp",
        "aud": "bloom-ci",
        "email": email,
        "exp": int(time.time()) + 300,
        **overrides,
    }
    token = jwt.encode(claims, CI_TUNNEL_KEY.read_text(), algorithm="ES256", headers={"kid": "ci-tunnel"})
    return f"Authorization: Bearer {token}"


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


def _python_get(
    command: list[str], url: str, *headers: str, python: str = "python"
) -> tuple[int | None, str]:
    result = subprocess.run(
        [*command, python, "-c", _PY_GET, url, *headers],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    status, _, body = result.stdout.partition("\n")
    if not status.strip():
        assert result.returncode == 0, f"request did not run: {result.stderr!r}"
        return None, body.strip()
    return int(status), body


def _from_bloommcp(container: str, url: str, *headers: str) -> tuple[int | None, str]:
    """A GET from inside the bloommcp container, which is on supanet."""
    return _python_get(["docker", "exec", container], url, *headers)


@pytest.fixture(scope="module")
def tunnel_client(bloommcp_container, caddy_tunnel_network, cloudflared_ip) -> list[str]:
    """A throwaway container at cloudflared's address on the tunnel network, from bloommcp's image."""
    image = subprocess.run(
        ["docker", "inspect", "-f", "{{.Config.Image}}", bloommcp_container],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not image:
        _unavailable("could not read the bloommcp image")
    return ["docker", "run", "--rm", "--network", caddy_tunnel_network, "--ip", cloudflared_ip, image]


def _through_tunnel(client: list[str], *headers: str) -> tuple[int, str]:
    status, body = _python_get(client, f"http://caddy:{TUNNEL_PORT}/mcp", *headers)
    assert status is not None, "no HTTP status from the tunnel entrance"
    return status, body


def test_the_entrance_drops_requests_from_supanet(bloommcp_container):
    # Control: Caddy is reachable from this container, so a dropped call below is the rule.
    reachable, _ = _from_bloommcp(bloommcp_container, "http://caddy:80/")
    assert reachable is not None, "caddy:80 did not answer from the bloommcp container"
    # Without the tunnel-only rule this would be bloommcp's 403 "No Bloom account".
    status, error = _from_bloommcp(
        bloommcp_container, f"http://caddy:{TUNNEL_PORT}/mcp", _bearer(NO_ACCOUNT)
    )
    assert status is None
    # Caddy's abort closes the connection; a DNS failure, refusal or timeout would not.
    assert any(s in error for s in ("RemoteDisconnected", "ConnectionReset", "reset by peer")), error


def test_a_salk_user_without_an_account_gets_bloommcps_refusal(tunnel_client):
    status, body = _through_tunnel(tunnel_client, _bearer(NO_ACCOUNT))
    assert status == 403
    assert f"No Bloom account for {NO_ACCOUNT}" in body, body


def test_a_refused_mcp_post_is_a_jsonrpc_error_with_the_message(tunnel_client):
    import json

    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    client = [*tunnel_client[:-1], "-e", f"POST_BODY={json.dumps(request)}", tunnel_client[-1]]
    status, body = _through_tunnel(
        client, _bearer(NO_ACCOUNT), "Content-Type: application/json"
    )
    assert status == 200, body
    response = json.loads(body)
    assert response["id"] == 1
    assert response["error"]["data"] == {"status": 403}
    assert f"No Bloom account for {NO_ACCOUNT}" in response["error"]["message"]


def test_a_token_for_another_audience_is_refused_live(tunnel_client):
    # Proves the deployed bloommcp verifies the token: a valid signature for prod is not enough.
    status, body = _through_tunnel(tunnel_client, _bearer(NO_ACCOUNT, aud="bloom-prod"))
    assert status == 403
    assert "could not be verified" in body, body


def test_a_forged_tunnel_header_through_caddy_is_replaced(tunnel_client):
    status, body = _through_tunnel(
        tunnel_client, _bearer(NO_ACCOUNT), "X-Bloom-Tunnel: forged"
    )
    assert status == 403
    assert f"No Bloom account for {NO_ACCOUNT}" in body, body


def test_a_forged_tunnel_header_straight_to_bloommcp_is_ignored(bloommcp_container):
    forged = _from_bloommcp(
        bloommcp_container,
        "http://bloommcp:8811/mcp",
        _bearer(NO_ACCOUNT),
        "X-Bloom-Tunnel: forged",
    )
    plain = _from_bloommcp(bloommcp_container, "http://bloommcp:8811/mcp")
    assert forged[0] == plain[0] == 401
    assert "No Bloom account" not in forged[1]


@pytest.fixture(scope="module")
def caddy_tunnel(caddy_container) -> tuple[str, str]:
    """Caddy's tunnel network and its address on it; a request to that address from inside
    Caddy comes from it, as cloudflared's do."""
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
            return name, ip
    _unavailable(f"caddy is not on a tunnel network: {result.stdout!r}")


@pytest.fixture(scope="module")
def caddy_tunnel_ip(caddy_tunnel) -> str:
    return caddy_tunnel[1]


@pytest.fixture(scope="module")
def caddy_tunnel_network(caddy_tunnel) -> str:
    return caddy_tunnel[0]


@pytest.fixture(scope="module")
def cloudflared_ip(caddy_container) -> str:
    """The one address :8880 accepts, as Caddy was given it."""
    env = subprocess.run(
        ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", caddy_container],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    for line in env:
        if line.startswith("TUNNEL_CLOUDFLARED_IP="):
            return line.split("=", 1)[1]
    _unavailable("caddy has no TUNNEL_CLOUDFLARED_IP")


def test_other_tunnel_network_addresses_are_dropped(tunnel_client, caddy_container, caddy_tunnel_ip):
    # Control: the same request from cloudflared's address is answered.
    assert _python_get(tunnel_client, f"http://caddy:{TUNNEL_PORT}/mcp")[0] == 403
    assert _request(caddy_container, f"http://{caddy_tunnel_ip}:{TUNNEL_PORT}/mcp") is None


@pytest.mark.skipif(sys.platform != "linux", reason="only a Linux host reaches container addresses")
def test_the_host_is_dropped_at_the_entrance(tunnel_client, caddy_tunnel_ip):
    """The host reaches the tunnel network through its gateway, an address inside the subnet."""
    assert _python_get(tunnel_client, f"http://caddy:{TUNNEL_PORT}/mcp")[0] == 403
    status, error = _python_get(
        [], f"http://{caddy_tunnel_ip}:{TUNNEL_PORT}/mcp", python=sys.executable
    )
    assert status is None
    assert any(s in error for s in ("RemoteDisconnected", "ConnectionReset", "reset by peer")), error


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
