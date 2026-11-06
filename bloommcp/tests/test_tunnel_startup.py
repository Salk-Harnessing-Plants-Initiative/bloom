"""Startup validation for the Salk connector's tunnel, and the tunnel through the real app.

The live test runs `server.build_app()` in a subprocess, because `bloom_mcp.auth`
reads `BLOOMMCP_API_KEY` once at import.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from bloom_mcp import tunnel

SECRET = "s" * 40
API_KEY = "test-api-key"


JWT_ENV = {
    "BLOOMMCP_TUNNEL_JWKS_URL": "https://airc.example/.well-known/jwks.json",
    "BLOOMMCP_TUNNEL_JWT_ISSUER": "bloom-mcp",
    "BLOOMMCP_TUNNEL_JWT_AUDIENCE": "bloom-test",
}


@pytest.fixture(autouse=True)
def _tunnel_env(monkeypatch):
    monkeypatch.setenv("BLOOMMCP_TUNNEL_SECRET", SECRET)
    monkeypatch.delenv("BLOOMMCP_TUNNEL_JWKS_FILE", raising=False)
    for name, value in JWT_ENV.items():
        monkeypatch.setenv(name, value)


# --- Startup validation -----------------------------------------------------


def test_validate_env_unset_secret_is_fine(monkeypatch):
    monkeypatch.delenv("BLOOMMCP_TUNNEL_SECRET")
    monkeypatch.delenv("BLOOMMCP_API_KEY", raising=False)
    tunnel.validate_env()


def test_validate_env_accepts_a_long_secret_with_an_api_key(monkeypatch):
    monkeypatch.setenv("BLOOMMCP_API_KEY", API_KEY)
    tunnel.validate_env()


def test_validate_env_refuses_a_short_secret(monkeypatch):
    monkeypatch.setenv("BLOOMMCP_API_KEY", API_KEY)
    monkeypatch.setenv("BLOOMMCP_TUNNEL_SECRET", "s" * (tunnel.MIN_SECRET_LENGTH - 1))
    with pytest.raises(RuntimeError, match="at least 32"):
        tunnel.validate_env()


def test_validate_env_refuses_a_secret_without_an_api_key(monkeypatch):
    monkeypatch.delenv("BLOOMMCP_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="BLOOMMCP_API_KEY"):
        tunnel.validate_env()


@pytest.mark.parametrize("missing", list(JWT_ENV))
def test_validate_env_refuses_a_secret_without_the_jwt_settings(monkeypatch, missing):
    monkeypatch.setenv("BLOOMMCP_API_KEY", API_KEY)
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match=missing):
        tunnel.validate_env()


def test_validate_env_accepts_a_keys_file_instead_of_the_url(monkeypatch):
    monkeypatch.setenv("BLOOMMCP_API_KEY", API_KEY)
    monkeypatch.delenv("BLOOMMCP_TUNNEL_JWKS_URL")
    monkeypatch.setenv("BLOOMMCP_TUNNEL_JWKS_FILE", "/run/keys/jwks.json")
    tunnel.validate_env()


def test_main_runs_the_tunnel_validation(monkeypatch):
    from bloom_mcp import server

    monkeypatch.setattr(server, "validate_data_env", lambda: None)
    monkeypatch.setattr(server, "validate_supabase_env", lambda: None)
    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)
    monkeypatch.setattr(sys, "argv", ["bloom-mcp"])
    monkeypatch.setenv("BLOOMMCP_TUNNEL_SECRET", "short")
    with pytest.raises(RuntimeError, match="BLOOMMCP_TUNNEL_SECRET"):
        server.main()


# --- Live: the real app and FastMCP's token check ---------------------------

_LIVE_SCRIPT = """
import json, os, sys, tempfile, time
import jwt
from cryptography.hazmat.primitives.asymmetric import ec

key = ec.generate_private_key(ec.SECP256R1())
jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
jwks = os.path.join(tempfile.mkdtemp(), "jwks.json")
with open(jwks, "w") as handle:
    json.dump({"keys": [{**jwk, "kid": "k", "alg": "ES256", "use": "sig"}]}, handle)

def token(email):
    claims = {"iss": "bloom-mcp", "aud": "bloom-test", "exp": int(time.time()) + 300, "email": email}
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "k"})

os.environ["BLOOMMCP_API_KEY"] = "test-api-key"
os.environ["BLOOMMCP_TUNNEL_SECRET"] = "s" * 40
os.environ["BLOOMMCP_TUNNEL_JWKS_FILE"] = jwks
os.environ["BLOOMMCP_TUNNEL_JWT_ISSUER"] = "bloom-mcp"
os.environ["BLOOMMCP_TUNNEL_JWT_AUDIENCE"] = "bloom-test"

from starlette.testclient import TestClient
import bloom_mcp.usage as usage
from bloom_mcp import supabase_client

recorded, lookups = [], []
usage.record_usage_async = lambda identity, action: recorded.append([identity, action])

def fake_rpc(name, params):
    lookups.append(params["p_email"])
    return "11111111-1111-1111-1111-111111111111"

supabase_client.call_rpc = fake_rpc

from bloom_mcp import server

INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "t", "version": "0"}},
}
base = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
out = {}
with TestClient(server.build_app()) as client:
    tunnel_hdrs = {**base, "X-Bloom-Tunnel": "s" * 40, "Authorization": "Bearer " + token("JDoe@salk.edu")}
    out["tunnel"] = client.post("/mcp", json=INIT, headers=tunnel_hdrs).status_code
    out["tunnel_recorded"] = list(recorded)
    recorded.clear()
    forged = {**base, "X-Bloom-Tunnel": "w" * 40, "Authorization": "Bearer " + token("director@salk.edu")}
    out["wrong_secret"] = client.post("/mcp", json=INIT, headers=forged).status_code
    no_secret = {**base, "Authorization": "Bearer " + token("director@salk.edu")}
    out["no_secret"] = client.post("/mcp", json=INIT, headers=no_secret).status_code
    out["health"] = client.get("/health").status_code
out["lookups"] = lookups
out["after_recorded"] = recorded
print(json.dumps(out))
"""


def test_tunnel_request_passes_fastmcp_and_forged_ones_do_not_live():
    result = subprocess.run(
        [sys.executable, "-c", _LIVE_SCRIPT], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["tunnel"] == 200, out
    assert out["tunnel_recorded"] == [
        ["11111111-1111-1111-1111-111111111111", "combined"]
    ]
    assert out["wrong_secret"] == 401, out
    assert out["no_secret"] == 401, out
    assert out["health"] == 200, out
    assert out["lookups"] == ["jdoe@salk.edu"]
    assert out["after_recorded"] == []
