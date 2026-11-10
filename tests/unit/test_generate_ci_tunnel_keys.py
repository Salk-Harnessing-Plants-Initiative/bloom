"""scripts/generate_ci_tunnel_keys.py: the private key it writes signs tokens its JWKS verifies."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import jwt

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_ci_tunnel_keys.py"


def _module():
    spec = importlib.util.spec_from_file_location("generate_ci_tunnel_keys", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_keys_sign_and_verify_a_tunnel_token(tmp_path):
    gen = _module()
    gen.main(str(tmp_path))
    claims = {"iss": "bloom-mcp", "aud": "bloom-ci", "email": "x@salk.edu", "exp": int(time.time()) + 60}
    token = jwt.encode(
        claims, (tmp_path / "key.pem").read_text(), algorithm="ES256", headers={"kid": gen.KID}
    )
    (key,) = jwt.PyJWKSet.from_json((tmp_path / "jwks.json").read_text()).keys
    assert key.key_id == gen.KID
    assert jwt.decode(token, key.key, algorithms=["ES256"], audience="bloom-ci", issuer="bloom-mcp")["email"] == "x@salk.edu"
