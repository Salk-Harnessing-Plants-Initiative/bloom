#!/usr/bin/env python3
"""Verify every variable mentioned in the compose is backed by a GitHub secret that exists.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_env_parity import discover_blocks, parse_block  # noqa: E402

# Environment name in deploy.yml's `.env.<name>` heredocs -> GitHub environment.
ENVIRONMENTS = {"prod": "production", 
                "staging": "staging"}

# Mirrors validate_env.sh's skip list; a unit test fails if the two drift.
EXCLUDED_KEYS = {"COMPOSE_PROJECT_NAME", "NEXT_PUBLIC_SUPABASE_COOKIE_NAME"}

COMPOSE_VAR = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)")
DEFAULTS_KEY = re.compile(r"^([A-Z][A-Z0-9_]*)=")

# Forms COMPOSE_VAR reads wrongly — compose must use plain ${UPPERCASE}.
UNBRACED_VAR = re.compile(r"(?<![$\w])\$([A-Za-z_][A-Za-z0-9_]*)")
LOWERCASE_BRACED = re.compile(r"\$\{([a-z][A-Za-z0-9_]*)")
ESCAPED_LITERAL = re.compile(r"\$\$\{([A-Za-z_][A-Za-z0-9_]*)")


def compose_required_keys(compose_text: str) -> set[str]:
    """Every `${VAR}` compose interpolates, minus the keys validate_env.sh skips.

    Mirrors validate_env.sh's derivation exactly: the same regex over the same
    file, so a var this reports is a var that script will demand at deploy time.
    """
    return set(COMPOSE_VAR.findall(compose_text)) - EXCLUDED_KEYS


def nonstandard_refs(compose_text: str) -> list[tuple[int, str, str]]:
    """Every reference that is not a plain ${UPPERCASE}, as (line, form, line_text)."""
    found = []
    for number, line in enumerate(compose_text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        for name in UNBRACED_VAR.findall(line):
            found.append((number, f"${name}", line.strip()))
        for name in LOWERCASE_BRACED.findall(line):
            found.append((number, "${" + name + "}", line.strip()))
        for name in ESCAPED_LITERAL.findall(line):
            found.append((number, "$${" + name + "}", line.strip()))
    return found


def defaults_keys(defaults_text: str) -> set[str]:
    """Keys a committed .env.<env>.defaults file supplies without a secret."""
    keys = set()
    for line in defaults_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = DEFAULTS_KEY.match(stripped)
        if match:
            keys.add(match.group(1))
    return keys


class Failures:
    """Collects failures so one run reports every problem, not just the first."""

    def __init__(self) -> None:
        self.count = 0

    def add(self, message: str, hint: str = "") -> None:
        self.count += 1
        print(f"ERROR: {message}", file=sys.stderr)
        if hint:
            print(f"       {hint}", file=sys.stderr)
        print(f"::error::{message}")


def check_environment(
    env_name: str,
    required: set[str],
    supplied_by_defaults: set[str],
    block_keys: dict[str, tuple[int, str, list[str]]],
    known_secrets: set[str] | None,
    failures: Failures,
) -> None:
    """Check one environment's heredoc covers everything compose requires."""
    github_env = ENVIRONMENTS[env_name]
    needed_from_secrets = sorted(required - supplied_by_defaults)

    for key in needed_from_secrets:
        entry = block_keys.get(key)
        if entry is None:
            failures.add(
                f"{key} is required by compose but is neither in "
                f".env.{env_name}.defaults nor set in deploy.yml's .env.{env_name} block",
                "Deploys will fail at env assembly until it is supplied.",
            )
            continue

        _line_no, _raw, secret_refs = entry
        if known_secrets is None:
            # Wiring-only mode: a key present in the block is as far as we can check.
            continue

        for ref in secret_refs:
            if ref not in known_secrets:
                failures.add(
                    f"{key} in deploy.yml's .env.{env_name} block reads "
                    f"${{{{ secrets.{ref} }}}}, but no secret named {ref} exists in "
                    f"the '{github_env}' environment",
                    f"Create {ref} in the {github_env} environment before merging, "
                    "or every deploy after this lands will fail.",
                )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", required=True, type=Path)
    parser.add_argument("--deploy", required=True, type=Path)
    parser.add_argument("--defaults-dir", type=Path, default=Path("."))
    parser.add_argument(
        "--secrets-json",
        type=Path,
        help="JSON of {github_environment: [secret names]}. Without it only wiring is checked.",
    )
    args = parser.parse_args(argv)

    for path in (args.compose, args.deploy):
        if not path.is_file():
            print(f"ERROR: not found: {path}", file=sys.stderr)
            return 2

    required = compose_required_keys(args.compose.read_text(encoding="utf-8"))
    if not required:
        # An empty required set would make every check below vacuously pass, so
        # treat it as a broken invocation rather than a clean bill of health.
        print(f"ERROR: no ${{VAR}} references found in {args.compose}", file=sys.stderr)
        return 2

    known: dict[str, set[str]] | None = None
    if args.secrets_json:
        if not args.secrets_json.is_file():
            print(f"ERROR: not found: {args.secrets_json}", file=sys.stderr)
            return 2
        raw = json.loads(args.secrets_json.read_text(encoding="utf-8"))
        known = {env: set(names) for env, names in raw.items()}

    blocks, _unexpected, _unclosed, _duplicates = discover_blocks(args.deploy)
    failures = Failures()

    # The offending line is deliberately not echoed: it is arbitrary file content.
    for number, form, _line in nonstandard_refs(args.compose.read_text(encoding="utf-8")):
        failures.add(
            f"{args.compose}:{number}: {form} is not a plain ${{UPPERCASE}} reference, "
            "so the required-keys check reads it wrongly",
            f"Use ${{{form.lstrip('$').strip('{}').upper()}}} instead.",
        )

    for env_name, github_env in ENVIRONMENTS.items():
        if env_name not in blocks:
            failures.add(f"deploy.yml has no .env.{env_name} secret-append block")
            continue

        defaults_path = args.defaults_dir / f".env.{env_name}.defaults"
        if not defaults_path.is_file():
            print(f"ERROR: not found: {defaults_path}", file=sys.stderr)
            return 2

        _start_line, body = blocks[env_name]
        block_keys, _duplicate_lhs = parse_block(body)

        check_environment(
            env_name,
            required,
            defaults_keys(defaults_path.read_text(encoding="utf-8")),
            block_keys,
            known.get(github_env, set()) if known is not None else None,
            failures,
        )

    if failures.count:
        print(f"\n{failures.count} problem(s) found.", file=sys.stderr)
        return 1

    mode = "wiring + secret existence" if known is not None else "wiring only"
    print(f"✓ {len(required)} compose-required keys backed in both environments ({mode})")
    if known is None:
        print(
            "::notice::Secret existence was not verified — no --secrets-json supplied. "
            "A var wired to a secret that does not exist would still pass this run."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
