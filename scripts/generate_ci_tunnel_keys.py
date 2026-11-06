"""Mint a throwaway ES256 key pair standing in for AIRC's tunnel JWT keys in CI.

Writes ``<dir>/jwks.json`` (the public keys bloommcp reads through
``BLOOMMCP_TUNNEL_JWKS_FILE``) and ``<dir>/key.pem`` (the private key the
integration tests sign tunnel tokens with). Generated per run; never stored.

Usage: python scripts/generate_ci_tunnel_keys.py <dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

KID = "ci-tunnel"


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    jwks = {"keys": [{**jwk, "kid": KID, "alg": "ES256", "use": "sig"}]}
    (out / "jwks.json").write_text(json.dumps(jwks), encoding="utf-8")
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    (out / "key.pem").write_bytes(pem)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
