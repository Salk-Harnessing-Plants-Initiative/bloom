"""bloommcp's own OAuth-protected-resource discovery route (RFC 9728).

No automated coverage previously existed for this route at all — it was
verified only by a one-time manual recording (per PR #613's description).
This closes that specific gap for the piece bloommcp itself serves; Caddy's
and Kong's forwarding of the surrounding routes needs a live stack and is
covered separately in `tests/integration/test_api_endpoints.py`.

Live subprocess, for the same reason as
`test_identity_middleware.py::test_identity_middleware_and_bearer_auth_are_independent_live`:
`bloom_mcp.auth.auth_provider` and its module-level `PUBLIC_URL`/
`AUTHORIZATION_SERVER` are built once at import time from whatever OAuth env
is set then, and every other test in this session already imports
`bloom_mcp.server` with OAuth unconfigured.
"""

from __future__ import annotations

import json
import subprocess
import sys

_DISCOVERY_SCRIPT = """
import os
os.environ["JWT_SECRET"] = "test-jwt-secret"
os.environ["BLOOMMCP_PUBLIC_URL"] = "https://bloom.salk.edu/bloommcp"
os.environ["BLOOMMCP_OAUTH_AUTHORIZATION_SERVER"] = "https://bloom.salk.edu/api/auth/v1"

import json
from starlette.testclient import TestClient
from bloom_mcp import server

with TestClient(server.build_app()) as client:
    r = client.get("/.well-known/oauth-protected-resource/bloommcp/mcp")

print(json.dumps({"status": r.status_code, "body": r.json() if r.status_code == 200 else None}))
"""


def test_oauth_protected_resource_discovery_route_is_served():
    """Empirically discovered path, not assumed: FastMCP's `RemoteAuthProvider`
    (`resource_name="Bloom MCP"`, `base_url=BLOOMMCP_PUBLIC_URL`) serves RFC
    9728 metadata at `/.well-known/oauth-protected-resource` + the resource's
    own path (`/bloommcp/mcp`, matching the combined surface's `/mcp` under
    `BLOOMMCP_PUBLIC_URL=.../bloommcp`) — not at the bare `/bloommcp` prefix,
    which 404s."""
    result = subprocess.run(
        [sys.executable, "-c", _DISCOVERY_SCRIPT],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["status"] == 200, out
    assert out["body"]["resource"] == "https://bloom.salk.edu/bloommcp/mcp"
    assert out["body"]["authorization_servers"] == [
        "https://bloom.salk.edu/api/auth/v1"
    ]
