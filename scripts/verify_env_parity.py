#!/usr/bin/env python3
"""Verify prod/staging env-block parity in a GitHub Actions deploy workflow.

Scope: structural check of the secret-append heredocs inside deploy.yml —

    f="…/.env.prod"
    cat >> "$f" << 'SECRETS'
      KEY=${{ secrets.PROD_KEY }}
      …
      SECRETS

…and the mirror block for `.env.staging`. Catches cross-prefix secret leaks,
composite-value drift, literal-vs-secret asymmetry, unexpected env blocks,
and malformed `${{ secrets.X }}` refs.

Usage:
    python3 scripts/verify_env_parity.py .github/workflows/deploy.yml

Exit 0: prod and staging are aligned. Exit 1: one or more parity violations
(each emits a human-readable stderr line and a GitHub Actions `::error`
annotation on stdout). Exit 2: usage or file-not-found error.
"""

import re
import sys
from pathlib import Path

# Match either legacy (`cat > .env.prod << 'EOF'`) or new-style append
# (`cat >> "$f" << 'SECRETS'`) heredoc starts. group(1) is the captured env
# name if the cat line names it directly; group(2) is the shell var (like `f`)
# if the cat line references one; group(3) is the terminator. Exactly one of
# group(1) / group(2) is populated on a match.
HEREDOC_START = re.compile(
    r"""cat\s*>>?\s*(?:
        [^<"'\s]*\.env\.(?P<env_inline>[a-z][a-z0-9_]*)
      |
        ["']?\$\{?(?P<shell_var>\w+)\}?["']?
    )\s*<<\s*'(?P<terminator>[A-Z_]+)'""",
    re.VERBOSE,
)
# Matches a shell assignment of a path ending in `.env.<env>`.
# Used to resolve the env name for heredocs that write through a variable.
VAR_ASSIGN = re.compile(
    r"""^\s*(?P<var>\w+)=["'][^"']*\.env\.(?P<env>[a-z][a-z0-9_]*)["']"""
)
LINE_PARSER = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
SECRET_REF = re.compile(r"\$\{\{\s*secrets\.([^}\s]+)\s*\}\}")
CANONICAL_REF = re.compile(r"^(PROD|STAGING)_[A-Z][A-Z0-9_]*$")


def discover_blocks(path: Path):
    """Walk the file, find every env heredoc, return (blocks, unexpected, unclosed, duplicates).

    blocks: {env_name: (start_line, body_lines)} for the FIRST prod/staging
            heredoc seen; any subsequent duplicate goes in `duplicates`.
    unexpected: [(start_line, env_name)] for env names other than prod/staging.
    unclosed: [(start_line, env_name)] for heredocs with no matching terminator.
    duplicates: [(start_line, env_name)] for the 2nd+ occurrence of .env.prod
                or .env.staging. Silently collapsing duplicates would hide the
                exact class of drift this check exists to catch, so we preserve
                the first and flag the rest.
    body_lines: list of (1-based line_number, raw_content) for every line
                between the opening marker and the terminator.
    """
    blocks: dict[str, tuple[int, list[tuple[int, str]]]] = {}
    unexpected: list[tuple[int, str]] = []
    unclosed: list[tuple[int, str]] = []
    duplicates: list[tuple[int, str]] = []

    text = path.read_text().splitlines()
    i = 0
    while i < len(text):
        m = HEREDOC_START.search(text[i])
        if not m:
            i += 1
            continue

        terminator = m.group("terminator")
        start_line = i + 1

        env_name = m.group("env_inline")
        if env_name is None:
            # Heredoc writes through a shell var (e.g. `cat >> "$f"`). Walk
            # back from this line to find the assignment that set that var
            # to a `.env.<env>` path, and use that env name.
            shell_var = m.group("shell_var")
            for k in range(i - 1, max(-1, i - 50), -1):
                am = VAR_ASSIGN.match(text[k])
                if am and am.group("var") == shell_var:
                    env_name = am.group("env")
                    break
            if env_name is None:
                # Indeterminate heredoc target — skip without flagging;
                # surrounding context (step name, job structure) would need
                # human review, not a mechanical parity check.
                i += 1
                continue

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
            if env_name in blocks:
                duplicates.append((start_line, env_name))
            else:
                blocks[env_name] = (start_line, body)
        else:
            unexpected.append((start_line, env_name))

        i = j + 1

    return blocks, unexpected, unclosed, duplicates


def parse_block(
    body: list[tuple[int, str]],
) -> tuple[
    dict[str, tuple[int, str, list[str]]],
    list[tuple[int, str, str]],
]:
    """Parse an env-heredoc body.

    Returns (parsed, duplicate_lhs):
      parsed: {lhs: (line_number, raw_line, [secret_refs])} keyed on the FIRST
              occurrence of each LHS — matches the prior API.
      duplicate_lhs: [(line_number, lhs, raw_line)] for every 2nd+ occurrence of
                     the same LHS within this block. Must be flagged, not silently
                     collapsed — the later declaration masks the earlier and any
                     divergence between them is invisible to downstream parity.
    """
    parsed: dict[str, tuple[int, str, list[str]]] = {}
    duplicate_lhs: list[tuple[int, str, str]] = []
    for line_no, raw in body:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = LINE_PARSER.match(stripped)
        if not m:
            continue
        lhs = m.group(1)
        refs = SECRET_REF.findall(raw)
        if lhs in parsed:
            duplicate_lhs.append((line_no, lhs, raw))
        else:
            parsed[lhs] = (line_no, raw, refs)
    return parsed, duplicate_lhs


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
    blocks, unexpected, unclosed, duplicate_blocks = discover_blocks(path)

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

    for start_line, env_name in duplicate_blocks:
        reporter.report(
            start_line,
            "duplicate env block",
            f"duplicate env block: a second .env.{env_name} heredoc was found "
            f"(the first would be silently overwritten; remove or merge the duplicate)",
        )

    if not unclosed and not unexpected and not duplicate_blocks and not blocks:
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

    prod, prod_duplicate_lhs = parse_block(blocks["prod"][1])
    staging, staging_duplicate_lhs = parse_block(blocks["staging"][1])

    for line_no, lhs, raw in prod_duplicate_lhs:
        reporter.report(
            line_no,
            "duplicate LHS",
            f"duplicate LHS in prod block: {lhs} declared more than once "
            f"(later declarations silently mask earlier ones)",
            raw,
        )
    for line_no, lhs, raw in staging_duplicate_lhs:
        reporter.report(
            line_no,
            "duplicate LHS",
            f"duplicate LHS in staging block: {lhs} declared more than once "
            f"(later declarations silently mask earlier ones)",
            raw,
        )

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
                    found.add(ref.removeprefix(prefix + "_"))
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
