"""Unit tests for scripts/generate_jwt_keys.py — the ES256 signing-key
generator required before an environment can enable OAuth's `openid` scope.

Previously untested, and its only non-stdlib import (`cryptography`) was not
declared in this project's own `pyproject.toml` — it happened to resolve
transitively via another dependency, so nothing in CI would have noticed if
some unrelated change in the dependency graph dropped it. Both closed here:
`cryptography` is now a direct test dependency, and this file exercises the
actual signing round-trip, not just that the script produces well-formed
JSON.
"""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "generate_jwt_keys.py"


def _load():
    spec = importlib.util.spec_from_file_location("generate_jwt_keys", _SCRIPT)
    assert spec and spec.loader, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load()


def _b64u_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _private_key_from_jwk(jwk: dict) -> ec.EllipticCurvePrivateKey:
    d = int.from_bytes(_b64u_decode(jwk["d"]), "big")
    return ec.derive_private_key(d, ec.SECP256R1())


def _public_key_from_jwk(jwk: dict) -> ec.EllipticCurvePublicKey:
    x = int.from_bytes(_b64u_decode(jwk["x"]), "big")
    y = int.from_bytes(_b64u_decode(jwk["y"]), "big")
    return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()


def test_build_returns_well_formed_json_for_both_outputs():
    keys_json, jwks_json = gen.build("test-secret")
    keys = json.loads(keys_json)
    jwks = json.loads(jwks_json)
    assert isinstance(keys, list) and len(keys) == 2
    assert isinstance(jwks, dict) and len(jwks["keys"]) == 2


def test_private_key_signs_a_token_the_public_jwk_verifies():
    """The actual point of JWT_KEYS/JWT_JWKS: a token signed with the
    emitted private key must verify against the emitted public key — not
    just that both are well-formed JSON."""
    keys_json, jwks_json = gen.build("test-secret")
    private_jwk = json.loads(keys_json)[0]
    public_jwk = json.loads(jwks_json)["keys"][0]

    private_key = _private_key_from_jwk(private_jwk)
    public_key = _public_key_from_jwk(public_jwk)

    token = jwt.encode({"sub": "test"}, private_key, algorithm="ES256")
    decoded = jwt.decode(token, public_key, algorithms=["ES256"])
    assert decoded["sub"] == "test"


def test_public_jwk_never_carries_the_private_d_value():
    _, jwks_json = gen.build("super-secret-value")
    public_jwk = json.loads(jwks_json)["keys"][0]
    assert "d" not in public_jwk


def test_legacy_symmetric_jwk_is_verify_only_in_both_outputs():
    """GoTrue accepts exactly one *signing* key and refuses to start with
    more ("multiple signing keys detected") — the legacy HS256 key must
    carry key_ops=["verify"], never "sign", in both JWT_KEYS and JWT_JWKS."""
    keys_json, jwks_json = gen.build("test-secret")
    legacy_in_keys = json.loads(keys_json)[1]
    legacy_in_jwks = json.loads(jwks_json)["keys"][1]
    assert legacy_in_keys["key_ops"] == ["verify"]
    assert legacy_in_jwks["key_ops"] == ["verify"]


def test_legacy_jwk_embeds_the_exact_secret_given():
    secret = "a-specific-jwt-secret-value"
    keys_json, _ = gen.build(secret)
    legacy = json.loads(keys_json)[1]
    assert _b64u_decode(legacy["k"]) == secret.encode()


def test_read_secret_raises_a_clear_error_with_no_input(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    with pytest.raises(SystemExit):
        gen.read_secret()


def test_read_secret_reads_from_env_var(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "  env-secret  ")
    assert gen.read_secret() == "env-secret"
