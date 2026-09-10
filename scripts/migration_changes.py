"""Which migration files a PR really changes.

A migration change is a path under supabase/migrations/ that the PR adds, modifies or
deletes and whose content at the PR head differs from staging (or is absent there).
Promotions from staging to main therefore have none, and a hotfix straight to main still
has its own.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

MIGRATIONS_DIR = "supabase/migrations/"


class RefError(RuntimeError):
    """A git ref needed for the comparison cannot be resolved."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _require(ref: str, repo: Path) -> None:
    if _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode != 0:
        raise RefError(f"cannot resolve git ref {ref!r}")


def _blob(ref: str, path: str, repo: Path) -> str | None:
    result = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}:{path}")
    return result.stdout.strip() if result.returncode == 0 else None


def changed_paths(base: str, head: str = "HEAD", repo: Path = Path(".")) -> list[str]:
    """Every path the PR touches since it left base. Renames count as both sides."""
    _require(base, repo)
    _require(head, repo)
    result = _git(repo, "diff", "--name-only", "--no-renames", f"{base}...{head}")
    if result.returncode != 0:
        raise RefError(result.stderr.strip() or f"git diff {base}...{head} failed")
    return sorted(line for line in result.stdout.splitlines() if line)


def migration_changes(
    base: str,
    head: str = "HEAD",
    staging_ref: str = "origin/staging",
    repo: Path = Path("."),
) -> list[str]:
    """Migration paths the PR changes whose content is not already on staging."""
    _require(staging_ref, repo)
    return [
        path
        for path in changed_paths(base, head, repo)
        if path.startswith(MIGRATIONS_DIR)
        and _blob(head, path, repo) != _blob(staging_ref, path, repo)
    ]
