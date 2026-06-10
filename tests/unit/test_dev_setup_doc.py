"""Setup docs and `make help` must reference only real Makefile targets.

DEV_SETUP.md and PROD_SETUP.md told developers to run `make apply-migrations` /
`make drop-tables`, and `make help` advertised `drop-tables` — none of which is a
defined rule (the real target is `make migrate-local`). This test resolves
referenced/advertised targets against actual rule DEFINITIONS in the Makefile, not
help text, so phantom targets fail.

Scope: in prose docs it matches only *hyphenated* `make <a-b>` tokens (so plain
English like "make sure" isn't a false positive); `make help` lines are a
controlled string, so any advertised target there is checked.
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

COMMAND_DOCS = sorted((REPO_ROOT / ".claude" / "commands").glob("*.md"))
# Migrations recorded by `supabase db push` live in
# `supabase_migrations.schema_migrations`; there has never been a top-level
# `_migrations` table. Match only SQL *usage* (FROM/JOIN/INTO/UPDATE/TABLE
# `_migrations`) — not prose that mentions the legacy name to say it's retired.
_LEGACY_MIGRATIONS_RE = re.compile(
    r"(?i)\b(?:from|join|into|update|table)\s+_migrations\b"
)
# A bare `docker exec db-dev` only works if compose sets `container_name` —
# neither docker-compose.dev.yml nor docker-compose.prod.yml does, so the real
# container is `<project>-db-dev-1`. Quick-start docs must use the service-aware
# `docker compose -f <file> exec db-*` so they work on a fresh clone.
_BARE_DOCKER_EXEC_RE = re.compile(r"docker\s+exec\s+(?:-\S+\s+)*db-(?:dev|prod)\b")


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


def test_command_docs_do_not_reference_legacy_migrations_table():
    """`.claude/commands/*` debug docs must query the real migration-tracking
    table (`supabase_migrations.schema_migrations`), not a nonexistent
    `_migrations` table that would error if a developer copy-pasted it."""
    problems = []
    for doc in COMMAND_DOCS:
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            if _LEGACY_MIGRATIONS_RE.search(line):
                problems.append(
                    f"{doc.name}:{i}: references nonexistent `_migrations` table "
                    f"(use supabase_migrations.schema_migrations)"
                )
    assert not problems, "legacy migration-table refs in command docs:\n" + "\n".join(problems)


def test_command_docs_use_compose_aware_exec_not_bare_docker_exec():
    """`.claude/commands/*` must not tell developers to `docker exec db-dev` — that
    fails on a fresh clone because compose sets no container_name. Use
    `docker compose -f <file> exec db-dev` (service-aware) instead."""
    problems = []
    for doc in COMMAND_DOCS + DOCS:
        if not doc.exists():
            continue
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            if _BARE_DOCKER_EXEC_RE.search(line):
                problems.append(
                    f"{doc.name}:{i}: bare `docker exec db-*` fails without "
                    f"container_name; use `docker compose -f <file> exec db-*`"
                )
    assert not problems, "bare docker exec in setup/command docs:\n" + "\n".join(problems)


def test_make_help_advertises_only_real_targets():
    real = _real_targets()
    text = MAKEFILE.read_text(encoding="utf-8")
    advertised = set(_HELP_TARGET_RE.findall(text))
    missing = sorted(t for t in advertised if t not in real)
    assert not missing, f"`make help` advertises non-existent targets: {missing}"
