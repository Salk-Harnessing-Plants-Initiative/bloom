#!/usr/bin/env python3
"""Verify prod/staging env-block parity in a GitHub Actions deploy workflow.

Scope: structural check of the `cat > .env.prod << 'ENVEOF' ... ENVEOF` and
`cat > .env.staging << 'ENVEOF' ... ENVEOF` heredocs inside a workflow YAML.
Catches cross-prefix secret leaks, composite-value drift, literal-vs-secret
asymmetry, unexpected env blocks, and malformed `${{ secrets.X }}` refs.

Authoritative spec:
    openspec/changes/update-env-parity-check/specs/deploy-env-parity/spec.md

Usage:
    python3 scripts/verify_env_parity.py .github/workflows/deploy.yml

Exit 0: prod and staging are aligned. Exit 1: one or more parity violations
(each emits a human-readable stderr line and a GitHub Actions `::error`
annotation on stdout). Exit 2: usage or file-not-found error.
"""

import re
import sys
from pathlib import Path

HEREDOC_START = re.compile(
    r"cat\s*>\s*[^<]*\.env\.([a-z][a-z0-9_]*)\s*<<\s*'([A-Z_]+)'"
)
LINE_PARSER = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
SECRET_REF = re.compile(r"\$\{\{\s*secrets\.([^}\s]+)\s*\}\}")
CANONICAL_REF = re.compile(r"^(PROD|STAGING)_[A-Z][A-Z0-9_]*$")


def discover_blocks(path: Path):
    """Walk the file, find every env heredoc, return (blocks, unexpected, unclosed).

    blocks: {env_name: (start_line, body_lines)} for prod/staging only.
    unexpected: [(start_line, env_name)] for env names other than prod/staging.
    unclosed: [(start_line, env_name)] for heredocs with no matching terminator.
    body_lines: list of (1-based line_number, raw_content) for every line
                between the opening marker and the terminator.
    """
    blocks: dict[str, tuple[int, list[tuple[int, str]]]] = {}
    unexpected: list[tuple[int, str]] = []
    unclosed: list[tuple[int, str]] = []

    text = path.read_text().splitlines()
    i = 0
    while i < len(text):
        m = HEREDOC_START.search(text[i])
        if not m:
            i += 1
            continue

        env_name = m.group(1)
        terminator = m.group(2)
        start_line = i + 1

        body: list[tuple[int, str]] = []
        j = i + 1
        found_close = False
        while j < len(text):
            if text[j].strip() == terminator:
                found_close = True
                break
            body.append((j + 1, text[j]))
            j += 1

        if not found_close:
            unclosed.append((start_line, env_name))
            i = j
            continue

        if env_name in ("prod", "staging"):
            blocks[env_name] = (start_line, body)
        else:
            unexpected.append((start_line, env_name))

        i = j + 1

    return blocks, unexpected, unclosed


def parse_block(body: list[tuple[int, str]]) -> dict[str, tuple[int, str, list[str]]]:
    """{lhs: (line_number, raw_line, [secret_refs])}."""
    parsed: dict[str, tuple[int, str, list[str]]] = {}
    for line_no, raw in body:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = LINE_PARSER.match(stripped)
        if not m:
            continue
        lhs = m.group(1)
        refs = SECRET_REF.findall(raw)
        parsed[lhs] = (line_no, raw, refs)
    return parsed


class ErrorReporter:
    def __init__(self, path: str):
        self.path = path
        self.ok = True

    def report(self, line: int, cls: str, description: str, offending: str = ""):
        self.ok = False
        print(f"{self.path}:{line}: {cls}: {description}", file=sys.stderr)
        if offending:
            print(offending, file=sys.stderr)
        print(f"::error file={self.path},line={line}::{cls}: {description}")


def main(path_str: str) -> int:
    path = Path(path_str)
    if not path.exists():
        print(f"ERROR: {path_str} not found", file=sys.stderr)
        return 2

    reporter = ErrorReporter(path_str)
    blocks, unexpected, unclosed = discover_blocks(path)

    for start_line, env_name in unclosed:
        reporter.report(
            start_line,
            "unclosed heredoc",
            f"unclosed heredoc for .env.{env_name}: no matching terminator",
        )

    for start_line, env_name in unexpected:
        reporter.report(
            start_line,
            "unexpected env block",
            f"unexpected env block: .env.{env_name} (only .env.prod and .env.staging are allowed)",
        )

    if not unclosed and not unexpected and not blocks:
        reporter.report(1, "0 env blocks found, expected 2", "0 env blocks found, expected 2")
        return 1

    if unclosed or not reporter.ok:
        return 1

    if "prod" not in blocks or "staging" not in blocks:
        missing = [e for e in ("prod", "staging") if e not in blocks]
        reporter.report(
            1,
            "env blocks incomplete",
            f"missing env blocks: {', '.join(missing)}",
        )
        return 1

    prod = parse_block(blocks["prod"][1])
    staging = parse_block(blocks["staging"][1])

    # (b) LHS parity
    prod_lhs = set(prod.keys())
    staging_lhs = set(staging.keys())
    for missing_lhs in sorted(prod_lhs - staging_lhs):
        line_no, raw, _ = prod[missing_lhs]
        reporter.report(
            line_no,
            "LHS missing",
            f"LHS missing from staging: {missing_lhs}",
            raw,
        )
    for missing_lhs in sorted(staging_lhs - prod_lhs):
        line_no, raw, _ = staging[missing_lhs]
        reporter.report(
            line_no,
            "LHS missing",
            f"LHS missing from prod: {missing_lhs}",
            raw,
        )

    # (c) RHS prefix correctness + malformed-ref detection
    def check_refs(parsed: dict, expected_prefix: str, block_name: str):
        for _lhs, (line_no, raw, refs) in parsed.items():
            for ref in refs:
                if not CANONICAL_REF.match(ref):
                    reporter.report(
                        line_no,
                        "malformed",
                        f"malformed secret reference: {ref}",
                        raw,
                    )
                elif not ref.startswith(expected_prefix + "_"):
                    reporter.report(
                        line_no,
                        "wrong-prefix",
                        f"wrong-prefix: {ref} appears in {block_name} block",
                        raw,
                    )

    check_refs(prod, "PROD", "prod")
    check_refs(staging, "STAGING", "staging")

    # (d) Suffix parity — only among well-formed refs with the expected prefix
    def suffixes(parsed: dict, prefix: str) -> set[str]:
        found: set[str] = set()
        for _lhs, (_line, _raw, refs) in parsed.items():
            for ref in refs:
                if CANONICAL_REF.match(ref) and ref.startswith(prefix + "_"):
                    found.add(ref[len(prefix) + 1 :])
        return found

    prod_suffixes = suffixes(prod, "PROD")
    staging_suffixes = suffixes(staging, "STAGING")

    def find_line_with_ref(parsed: dict, ref_name: str) -> tuple[int, str]:
        for _lhs, (line_no, raw, refs) in parsed.items():
            if ref_name in refs:
                return line_no, raw
        return 1, ""

    for suffix in sorted(prod_suffixes - staging_suffixes):
        line_no, raw = find_line_with_ref(prod, f"PROD_{suffix}")
        reporter.report(
            line_no,
            "missing from staging",
            f"missing from staging: suffix {suffix} (prod uses PROD_{suffix}, staging has no STAGING_{suffix})",
            raw,
        )
    for suffix in sorted(staging_suffixes - prod_suffixes):
        line_no, raw = find_line_with_ref(staging, f"STAGING_{suffix}")
        reporter.report(
            line_no,
            "missing from prod",
            f"missing from prod: suffix {suffix} (staging uses STAGING_{suffix}, prod has no PROD_{suffix})",
            raw,
        )

    # (e) Per-LHS secret-ref consistency
    for lhs in sorted(prod_lhs & staging_lhs):
        prod_has_ref = bool(prod[lhs][2])
        staging_has_ref = bool(staging[lhs][2])
        if prod_has_ref != staging_has_ref:
            if prod_has_ref:
                line_no, raw, _ = staging[lhs]
                side = "staging uses literal while prod uses secret ref"
            else:
                line_no, raw, _ = prod[lhs]
                side = "prod uses literal while staging uses secret ref"
            reporter.report(
                line_no,
                "inconsistent",
                f"inconsistent secret use for {lhs}: {side}",
                raw,
            )

    if not reporter.ok:
        return 1

    n_vars = len(prod_lhs | staging_lhs)
    m_refs = sum(len(r) for _, _, r in prod.values()) + sum(
        len(r) for _, _, r in staging.values()
    )
    print(f"OK \u2014 {n_vars} vars, {m_refs} secret refs, prod and staging aligned")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: verify_env_parity.py <path-to-deploy.yml>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
