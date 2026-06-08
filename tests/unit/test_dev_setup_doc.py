"""Setup docs and `make help` must reference only real Makefile targets.

DEV_SETUP.md and PROD_SETUP.md told developers to run `make apply-migrations` /
`make drop-tables`, and `make help` advertised `drop-tables` — none of which is a
defined rule (the real target is `make migrate-local`). This test resolves every
referenced/advertised target against actual rule DEFINITIONS in the Makefile, not
help text, so phantom targets fail.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
DOCS = [REPO_ROOT / "DEV_SETUP.md", REPO_ROOT / "PROD_SETUP.md", REPO_ROOT / "README.md"]

# A `make <token>` reference where token is hyphenated (e.g. apply-migrations,
# migrate-local). Requiring a hyphen avoids English false positives like
# "make sure" while still catching every real/phantom multi-word target.
_DOC_TARGET_RE = re.compile(r"\bmake\s+([a-z0-9]+(?:-[a-z0-9]+)+)\b")
# `make help` advertises targets as: @echo "  make <token> ...". help is a
# controlled string, so match any token here.
_HELP_TARGET_RE = re.compile(r'@echo\s+"\s*make\s+([a-zA-Z0-9_-]+)')


def _real_targets() -> set[str]:
    """Target names that have an actual rule definition (`name:` at col 0)."""
    targets = set()
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([a-zA-Z0-9_][a-zA-Z0-9_-]*):", line)
        if m:
            targets.add(m.group(1))
    return targets


def test_docs_reference_only_real_make_targets():
    real = _real_targets()
    problems = []
    for doc in DOCS:
        if not doc.exists():
            continue
        for tgt in set(_DOC_TARGET_RE.findall(doc.read_text(encoding="utf-8"))):
            if tgt not in real:
                problems.append(f"{doc.name}: `make {tgt}` has no Makefile rule")
    assert not problems, "phantom make targets referenced in docs:\n" + "\n".join(problems)


def test_make_help_advertises_only_real_targets():
    real = _real_targets()
    text = MAKEFILE.read_text(encoding="utf-8")
    advertised = set(_HELP_TARGET_RE.findall(text))
    missing = sorted(t for t in advertised if t not in real)
    assert not missing, f"`make help` advertises non-existent targets: {missing}"
