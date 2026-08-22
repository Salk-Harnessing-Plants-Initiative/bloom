#!/usr/bin/env python3
"""Generate the JWT signing keys Supabase Auth needs to issue ID tokens.

Supabase cannot sign an ID token with the shared HS256 secret — a public
OAuth client has no way to verify one without also holding that secret, so
GoTrue refuses and any client requesting the `openid` scope fails at token
exchange. Signing therefore has to move to an asymmetric key pair.

This prints two values:

    JWT_KEYS   the EC P-256 *private* key plus the existing JWT_SECRET.
               Only Supabase Auth gets this; it is what signs new tokens.

    JWT_JWKS   the EC *public* half plus the existing JWT_SECRET, so both new
               ES256 and older HS256 tokens validate.

               Each verifier reads key material from a *different* variable, and
               only PostgREST accepts it inside its secret. Wiring JWT_JWKS into
               the wrong one leaves that service HS256-only, which is how #646
               broke Storage:

                   rest       PGRST_JWT_SECRET: ${JWT_JWKS:-${JWT_SECRET}}
                   storage    JWT_JWKS      (PGRST_JWT_SECRET stays symmetric)
                   realtime   API_JWT_JWKS  (API_JWT_SECRET stays symmetric)

               Supavisor has no documented JWKS input; leave it on JWT_SECRET.

BOTH VALUES ARE SECRETS. `JWT_JWKS` reads like public key material, but it
embeds JWT_SECRET as a symmetric JWK so pre-migration tokens keep working —
publishing it would publish that secret. Store both in GitHub Secrets
alongside JWT_SECRET; never in the committed `.env.*.defaults` files.

Generate a distinct pair per environment — dev, staging and prod must not
share a signing key.

The secret is read from the JWT_SECRET environment variable, or from stdin.
It is deliberately not accepted as a command-line argument, which would leave
it in shell history and visible in `ps` output while the script runs.

Usage:
    JWT_SECRET=... python scripts/generate_jwt_keys.py
    # or, reading the secret straight off a deployed env file:
    grep '^JWT_SECRET=' .env.prod | cut -d= -f2- | python scripts/generate_jwt_keys.py
"""

import base64
import json
import os
import sys
import uuid

from cryptography.hazmat.primitives.asymmetric import ec

P256_COORD_BYTES = 32


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def ec_private_jwk(kid: str) -> dict:
    """A fresh EC P-256 signing key."""
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.private_numbers()
    public = numbers.public_numbers
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64u(public.x.to_bytes(P256_COORD_BYTES, "big")),
        "y": b64u(public.y.to_bytes(P256_COORD_BYTES, "big")),
        "d": b64u(numbers.private_value.to_bytes(P256_COORD_BYTES, "big")),
        "kid": kid,
        "alg": "ES256",
        "use": "sig",
        "key_ops": ["sign", "verify"],
    }


def legacy_symmetric_jwk(secret: str) -> dict:
    """The existing shared secret, verify-only.

    GoTrue accepts exactly one *signing* key and refuses to start with more
    ("multiple signing keys detected"), so this carries `verify` alone. New
    tokens are signed by the EC key; tokens already issued under the shared
    secret keep validating until they expire.
    """
    return {
        "kty": "oct",
        "k": b64u(secret.encode()),
        "kid": "legacy-hs256",
        "alg": "HS256",
        "use": "sig",
        "key_ops": ["verify"],
    }


def build(secret: str) -> tuple[str, str]:
    private = ec_private_jwk(str(uuid.uuid4()))
    legacy = legacy_symmetric_jwk(secret)

    public = {k: v for k, v in private.items() if k != "d"}
    public["key_ops"] = ["verify"]

    jwt_keys = [private, legacy]
    jwt_jwks = {"keys": [public, legacy]}
    compact = {"separators": (",", ":")}
    return json.dumps(jwt_keys, **compact), json.dumps(jwt_jwks, **compact)


def read_secret() -> str:
    """The environment's existing JWT_SECRET, from the env var or stdin."""
    secret = os.environ.get("JWT_SECRET", "").strip()
    if secret:
        return secret
    if not sys.stdin.isatty():
        secret = sys.stdin.read().strip()
    if not secret:
        raise SystemExit(
            "No secret supplied. Set JWT_SECRET, or pipe it in:\n"
            "  grep '^JWT_SECRET=' .env.prod | cut -d= -f2- | "
            f"python {sys.argv[0]}"
        )
    return secret


def main() -> None:
    keys, jwks = build(read_secret())
    print(f"JWT_KEYS={keys}")
    print(f"JWT_JWKS={jwks}")


if __name__ == "__main__":
    main()
