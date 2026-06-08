#!/usr/bin/env python3
"""Generate a local ``.env.dev`` from ``.env.dev.example`` with fresh secrets.

Usage (cross-platform — macOS, Linux, WSL2):

    make init                  # the documented entry point
    # or directly:
    uv run --with pyjwt,python-dotenv python scripts/init_dev.py [--force]

What it does (issue #104):

- Copies ``.env.dev.example`` to ``.env.dev`` and replaces every ``CHANGEME``
  value with a freshly generated local secret.
- ``ANON_KEY``, ``SERVICE_ROLE_KEY``, ``BLOOM_AGENT_KEY`` (and
  ``NEXT_PUBLIC_SUPABASE_ANON_KEY``) are minted as HS256 JWTs **signed by the
  generated ``JWT_SECRET``** with the right ``role`` claim — independent random
  strings would be rejected by GoTrue/PostgREST.
- Encryption-key sizes mirror the verified ``scripts/generate-secrets.sh``
  (notably ``DB_ENC_KEY`` = exactly 16 bytes for Realtime AES-128).
- Idempotent: refuses to overwrite an existing ``.env.dev`` without ``--force``;
  ``--force`` backs the old file up first (timestamped if a backup exists).
- Never prints secret values.

These credentials are for the LOCAL stack only and never leave the machine;
``.env.dev`` stays git-ignored.
"""
from __future__ import annotations

import argparse
import secrets
import sys
import time
from pathlib import Path

import jwt  # PyJWT

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = REPO_ROOT / ".env.dev.example"
DEFAULT_OUTPUT = REPO_ROOT / ".env.dev"

PLACEHOLDER = "CHANGEME"
_JWT_EXP_SECONDS = 5 * 365 * 24 * 3600  # 5 years

# JWT role claim per generated key.
_JWT_ROLES = {
    "ANON_KEY": "anon",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY": "anon",
    "SERVICE_ROLE_KEY": "service_role",
    "BLOOM_AGENT_KEY": "bloom_agent",
}


def _mint_jwt(role: str, secret: str, now: int) -> str:
    token = jwt.encode(
        {
            "role": role,
            "iss": "supabase",
            "aud": "authenticated",
            "iat": now,
            "exp": now + _JWT_EXP_SECONDS,
        },
        secret,
        algorithm="HS256",
    )
    # PyJWT >=2 returns str; older returns bytes.
    return token.decode() if isinstance(token, bytes) else token


def generate_secrets(now: int | None = None) -> dict[str, str]:
    """Return all generated values for the local stack.

    Key sizes mirror ``scripts/generate-secrets.sh``: ``DB_ENC_KEY`` exactly 16
    bytes (Realtime AES-128), ``VAULT_ENC_KEY`` 32, ``SECRET_KEY_BASE`` >=64,
    ``SUPAVISOR_ENC_KEY`` 64 hex, ``JWT_SECRET`` >=32. The ANON/SERVICE/AGENT
    keys are JWTs signed by the generated ``JWT_SECRET``.
    """
    if now is None:
        now = int(time.time())
    jwt_secret = secrets.token_hex(32)  # 64 hex chars (>=32)
    values: dict[str, str] = {
        "POSTGRES_PASSWORD": secrets.token_hex(16),   # 32 hex
        "JWT_SECRET": jwt_secret,
        "SUPAVISOR_ENC_KEY": secrets.token_hex(32),   # 64 hex
        "VAULT_ENC_KEY": secrets.token_hex(16),       # 32 hex
        "SECRET_KEY_BASE": secrets.token_hex(32),     # 64 hex (>=64)
        "DB_ENC_KEY": secrets.token_hex(8),           # 16 hex = 16 bytes
        "MINIO_ROOT_PASSWORD": secrets.token_hex(12),  # 24 hex
        "DASHBOARD_PASSWORD": secrets.token_hex(8),   # 16 hex
        "BLOOMMCP_API_KEY": secrets.token_hex(16),    # 32 hex
    }
    for key, role in _JWT_ROLES.items():
        values[key] = _mint_jwt(role, jwt_secret, now)
    # LANGCHAIN_POSTGRES_URL embeds the generated Postgres password.
    values["LANGCHAIN_POSTGRES_URL"] = (
        f"postgresql://supabase_admin:{values['POSTGRES_PASSWORD']}@db-dev:5432/postgres"
    )
    return values


def render(template_text: str, values: dict[str, str]) -> str:
    """Rewrite generated keys with their values, preserving comments and order.

    Tolerates CRLF templates; always emits LF. Keys not in ``values`` (plain
    config, user-supplied blanks) are passed through untouched.
    """
    out: list[str] = []
    for raw in template_text.splitlines():
        line = raw.rstrip("\r")
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in values:
                out.append(f"{key}={values[key]}")
                continue
        out.append(line)
    return "\n".join(out) + "\n"


def _backup_path(output: Path, now: int) -> Path:
    backup = output.parent / (output.name + ".backup")
    if backup.exists():
        ts = time.strftime("%Y%m%d%H%M%S", time.localtime(now))
        backup = output.parent / (output.name + f".backup.{ts}")
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a local .env.dev from .env.dev.example."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing .env.dev (backed up first)",
    )
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    if not args.template.exists():
        print(f"error: template not found: {args.template}", file=sys.stderr)
        return 1
    template_text = args.template.read_text(encoding="utf-8")
    if PLACEHOLDER not in template_text:
        print(
            f"error: template has no {PLACEHOLDER} markers (is {args.template} "
            f"the right file?)",
            file=sys.stderr,
        )
        return 1

    if args.output.exists() and not args.force:
        print(
            f"error: {args.output} already exists. Re-run with --force to "
            f"overwrite (the existing file is backed up first).",
            file=sys.stderr,
        )
        return 1

    now = int(time.time())
    if args.output.exists() and args.force:
        backup = _backup_path(args.output, now)
        args.output.replace(backup)
        print(f"Backed up existing .env.dev -> {backup.name}")

    values = generate_secrets(now)
    args.output.write_text(render(template_text, values), encoding="utf-8", newline="\n")

    print(f"Wrote {args.output} with {len(values)} generated local secrets.")
    print("Generated keys: " + ", ".join(sorted(values)))
    print(
        "Next: set OPENAI_API_KEY / LANGCHAIN_API_KEY if you need those features, "
        "then `make dev-up`."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
