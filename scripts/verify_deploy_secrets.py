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

from verify_env_parity import SECRET_REF, discover_blocks  # noqa: E402

# Environment name in deploy.yml's `.env.<name>` heredocs -> GitHub environment.
ENVIRONMENTS = {"prod": "production", "staging": "staging"}

# Mirrors validate_env.sh's skip list; a unit test fails if the two drift.
EXCLUDED_KEYS = {"COMPOSE_PROJECT_NAME", "NEXT_PUBLIC_SUPABASE_COOKIE_NAME"}

COMPOSE_VAR = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)")
# validate_env.sh:76 accepts a key only with a non-empty, non-comment value, anchored
# at column 0. Matching on the name alone would pass `KEY=`, `KEY=#TODO` and `  KEY=v`,
# all of which fail the deploy.
SUPPLIED_KEY = re.compile(r"^([A-Z_][A-Z0-9_]*)=[^\s#]")
# The marker validate_env.sh requires as the env file's last line; a key written below
# it is silently dropped from the check but truncates the file at deploy time.
EOF_MARKER = "# _EOF_MARKER_"

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


def nonstandard_refs(compose_text: str) -> list[tuple[int, str]]:
    """Every reference that is not a plain ${UPPERCASE}, as (line_number, form)."""
    found = []
    for number, raw in enumerate(compose_text.splitlines(), start=1):
        # Comments are not interpolated, so a $VAR mentioned in prose is not a finding.
        # Only whole-line comments are stripped from compose_required_keys' view — see
        # its docstring; here the trailing form is dropped too, to avoid failing a PR
        # over a note in the margin.
        line = raw.split(" #", 1)[0] if " #" in raw else raw
        if line.lstrip().startswith("#") or not line.strip():
            continue
        for name in UNBRACED_VAR.findall(line):
            found.append((number, f"${name}"))
        for name in LOWERCASE_BRACED.findall(line):
            found.append((number, "${" + name + "}"))
        for name in ESCAPED_LITERAL.findall(line):
            found.append((number, "$${" + name + "}"))
    return found


def defaults_keys(defaults_text: str) -> set[str]:
    """Keys a committed .env.<env>.defaults file supplies with a usable value."""
    return {
        match.group(1)
        for line in defaults_text.splitlines()
        if (match := SUPPLIED_KEY.match(line))
    }


def block_entries(body: list[tuple[int, str]]) -> tuple[dict[str, list[str]], list[str]]:
    """Keys a deploy.yml heredoc supplies, and keys written below the EOF marker.

    Lines are dedented by the block's own indent, matching what the YAML block scalar
    does at deploy time, so a line indented deeper than its neighbours is caught here
    rather than at env assembly.
    """
    raw = [line for _number, line in body]
    indents = [len(line) - len(line.lstrip()) for line in raw if line.strip()]
    margin = min(indents) if indents else 0

    supplied: dict[str, list[str]] = {}
    after_marker: list[str] = []
    seen_marker = False
    for line in raw:
        dedented = line[margin:] if line[:margin].isspace() or not line[:margin] else line
        if dedented.strip() == EOF_MARKER:
            seen_marker = True
            continue
        match = SUPPLIED_KEY.match(dedented)
        if not match:
            continue
        key = match.group(1)
        if seen_marker:
            after_marker.append(key)
        # A duplicate LHS is written twice by `cat >>`; the last one wins at deploy
        # time, so later refs replace earlier ones rather than being discarded.
        supplied[key] = SECRET_REF.findall(dedented)
    return supplied, after_marker


def load_secret_names(path: Path) -> dict[str, set[str]] | None:
    """Secret names per GitHub environment, or None if the file is unusable.

    Shape is validated rather than trusted: a string value would silently become a
    set of characters and report every secret as missing, and a missing environment
    key would assert an absence the file never claimed.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"ERROR: {path} is not readable JSON: {exc}", file=sys.stderr)
        return None

    if not isinstance(raw, dict):
        print(f"ERROR: {path} must be an object, got {type(raw).__name__}", file=sys.stderr)
        return None

    names: dict[str, set[str]] = {}
    for github_env in ENVIRONMENTS.values():
        if github_env not in raw:
            print(f"ERROR: {path} has no entry for '{github_env}'", file=sys.stderr)
            return None
        entry = raw[github_env]
        if not isinstance(entry, list) or not all(isinstance(n, str) for n in entry):
            print(
                f"ERROR: {path}['{github_env}'] must be a list of strings",
                file=sys.stderr,
            )
            return None
        names[github_env] = set(entry)
    return names


def hint_for(form: str) -> str:
    """Remediation for a rejected reference.

    `$$` is compose's escape for a literal `$`, so the braced form is not a variable
    the env file can supply — suggesting `${NAME}` would move expansion from the
    container to the deploy host and quietly change what the container runs.
    """
    name = form.lstrip("$").strip("{}").upper()
    if form.startswith("$$"):
        return (
            "$$ is compose's escape for a literal $, not a variable the env file "
            f"supplies. Write $${name} if the container should expand it, "
            f"or ${{{name}}} if the deploy should."
        )
    return f"Use ${{{name}}} instead."


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
    supplied_by_block: dict[str, list[str]],
    after_marker: list[str],
    known_secrets: set[str] | None,
    failures: Failures,
) -> None:
    """Check one environment's heredoc covers everything compose requires."""
    github_env = ENVIRONMENTS[env_name]

    for key in sorted(after_marker):
        failures.add(
            f"{key} is written below {EOF_MARKER} in deploy.yml's .env.{env_name} block",
            "validate_env.sh requires that marker to be the file's last line and rejects "
            "the whole env file as truncated. Move the key above it.",
        )

    for key in sorted(required - supplied_by_defaults):
        refs = supplied_by_block.get(key)
        if refs is None:
            failures.add(
                f"{key} is required by compose but is neither in "
                f".env.{env_name}.defaults nor set with a value in deploy.yml's "
                f".env.{env_name} block",
                "Deploys will fail at env assembly until it is supplied.",
            )
            continue

        if known_secrets is None:
            # Wiring-only mode: a key present in the block is as far as we can check.
            continue

        for ref in refs:
            if ref not in known_secrets:
                failures.add(
                    f"{key} in deploy.yml's .env.{env_name} block reads "
                    f"${{{{ secrets.{ref} }}}}, but no secret named {ref} exists as a "
                    f"repository secret or in the '{github_env}' environment",
                    f"Create {ref} before merging, or every deploy after this lands "
                    "will fail.",
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

    # Every input is checked up front: a missing file discovered mid-run would abandon
    # findings already reported and skip whichever environment had not been reached.
    inputs = [args.compose, args.deploy]
    inputs += [args.defaults_dir / f".env.{env}.defaults" for env in ENVIRONMENTS]
    for path in inputs:
        if not path.is_file():
            print(f"ERROR: not found: {path}", file=sys.stderr)
            return 2

    try:
        compose_text = args.compose.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"ERROR: {args.compose} is not valid UTF-8: {exc}", file=sys.stderr)
        return 2

    failures = Failures()

    # The lint runs before the empty-required guard below: a compose file written
    # entirely with unbraced refs yields no required keys, and reporting "no ${VAR}
    # references found" would hide the very mistake this lint exists to name.
    for number, form in nonstandard_refs(compose_text):
        failures.add(
            f"{args.compose}:{number}: {form} is not a plain ${{UPPERCASE}} reference, "
            "so the required-keys check reads it wrongly",
            hint_for(form),
        )

    required = compose_required_keys(compose_text)
    if not required and not failures.count:
        # An empty required set would make every check below vacuously pass.
        print(f"ERROR: no ${{VAR}} references found in {args.compose}", file=sys.stderr)
        return 2

    known: dict[str, set[str]] | None = None
    if args.secrets_json:
        known = load_secret_names(args.secrets_json)
        if known is None:
            return 2

    blocks, unexpected, unclosed, duplicates = discover_blocks(args.deploy)
    # Dropping these would produce confident, misdirected messages: an unclosed prod
    # heredoc swallows the staging block, which then reads as simply absent.
    for line_number, env_name in unclosed:
        failures.add(f"deploy.yml:{line_number}: unclosed .env.{env_name} heredoc")
    for line_number, env_name in duplicates:
        failures.add(f"deploy.yml:{line_number}: duplicate .env.{env_name} heredoc")
    for line_number, env_name in unexpected:
        failures.add(f"deploy.yml:{line_number}: unexpected .env.{env_name} heredoc")

    for env_name, github_env in ENVIRONMENTS.items():
        if env_name not in blocks:
            failures.add(f"deploy.yml has no .env.{env_name} secret-append block")
            continue

        _start_line, body = blocks[env_name]
        supplied_by_block, after_marker = block_entries(body)
        defaults_path = args.defaults_dir / f".env.{env_name}.defaults"

        check_environment(
            env_name,
            required,
            defaults_keys(defaults_path.read_text(encoding="utf-8")),
            supplied_by_block,
            after_marker,
            known.get(github_env) if known is not None else None,
            failures,
        )

    if failures.count:
        print(f"\n{failures.count} problem(s) found.", file=sys.stderr)
        return 1

    mode = "wiring + secret existence" if known is not None else "wiring only"
    verb = "backed" if known is not None else "wired"
    print(f"✓ {len(required)} compose-required keys {verb} in both environments ({mode})")
    if known is None:
        print(
            "::notice::Secret existence was not verified — no --secrets-json supplied. "
            "A var wired to a secret that does not exist would still pass this run."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
