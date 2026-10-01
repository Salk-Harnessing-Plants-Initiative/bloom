"""Tests for scripts/migration_changes.py: which migration files a PR really changes.

A migration change is a path under supabase/migrations/ that the PR adds, modifies or
deletes and whose content is not already on staging. Promotions from staging to main
therefore have none, while a hotfix straight to main still does.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "migration_changes", REPO_ROOT / "scripts" / "migration_changes.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["migration_changes"] = module
    spec.loader.exec_module(module)
    return module


mc = _load()

M1 = "supabase/migrations/20260101000000_one.sql"
M2 = "supabase/migrations/20260102000000_two.sql"
M3 = "supabase/migrations/20260103000000_three.sql"
HOTFIX = "supabase/migrations/20260104000000_hotfix.sql"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """main has M1; staging adds M2; the caller branches from there."""
    _git(tmp_path, "init", "-q", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    _write(tmp_path, M1, "SELECT 1;\n")
    _commit(tmp_path, "m1")
    _git(tmp_path, "checkout", "-q", "-b", "staging")
    _write(tmp_path, M2, "SELECT 2;\n")
    _write(tmp_path, "README.md", "hi\n")
    _commit(tmp_path, "m2")
    return tmp_path


def _changes(repo: Path, base: str, head: str) -> list[str]:
    return mc.migration_changes(base, head, staging_ref="staging", repo=repo)


def test_staging_pr_yields_its_migrations(repo):
    _git(repo, "checkout", "-q", "-b", "feature", "staging")
    _write(repo, M3, "SELECT 3;\n")
    _write(repo, "web/x.ts", "export {};\n")
    _commit(repo, "feature")
    assert _changes(repo, "staging", "feature") == [M3]


def test_promotion_yields_none(repo):
    assert _changes(repo, "main", "staging") == []


def test_promotion_carrying_a_deletion_yields_none(repo):
    _git(repo, "rm", "-q", M1)
    _commit(repo, "staging deletes m1")
    assert _changes(repo, "main", "staging") == []


def test_hotfix_to_main_yields_its_migration(repo):
    _git(repo, "checkout", "-q", "-b", "hotfix", "main")
    _write(repo, HOTFIX, "SELECT 4;\n")
    _commit(repo, "hotfix")
    assert _changes(repo, "main", "hotfix") == [HOTFIX]


def test_modifying_a_staged_migration_counts(repo):
    _git(repo, "checkout", "-q", "-b", "feature", "staging")
    _write(repo, M2, "SELECT 22;\n")
    _commit(repo, "edit m2")
    assert _changes(repo, "staging", "feature") == [M2]


def test_deleting_a_migration_counts(repo):
    _git(repo, "checkout", "-q", "-b", "feature", "staging")
    _git(repo, "rm", "-q", M2)
    _commit(repo, "delete m2")
    assert _changes(repo, "staging", "feature") == [M2]


def test_moving_a_migration_out_of_the_folder_counts(repo):
    _git(repo, "checkout", "-q", "-b", "feature", "staging")
    (repo / "supabase" / "archive").mkdir(parents=True)
    _git(repo, "mv", M2, "supabase/archive/two.sql")
    _commit(repo, "move m2")
    assert _changes(repo, "staging", "feature") == [M2]


def test_non_migration_changes_are_ignored(repo):
    _git(repo, "checkout", "-q", "-b", "feature", "staging")
    _write(repo, "web/x.ts", "export {};\n")
    _commit(repo, "web only")
    assert _changes(repo, "staging", "feature") == []


def test_changed_paths_reports_both_sides_of_a_rename(repo):
    _git(repo, "checkout", "-q", "-b", "feature", "staging")
    _write(repo, "web/a.ts", "export const a = 1;\n")
    _commit(repo, "add a")
    _git(repo, "checkout", "-q", "-b", "moved")
    (repo / "tests" / "unit").mkdir(parents=True)
    _git(repo, "mv", "web/a.ts", "tests/unit/a.ts")
    _commit(repo, "move a")
    assert mc.changed_paths("feature", "moved", repo=repo) == ["tests/unit/a.ts", "web/a.ts"]


def test_unresolvable_base_raises(repo):
    with pytest.raises(mc.RefError):
        mc.migration_changes("no-such-ref", "staging", staging_ref="staging", repo=repo)


def test_unresolvable_staging_ref_raises(repo):
    with pytest.raises(mc.RefError):
        mc.migration_changes("main", "staging", staging_ref="origin/nope", repo=repo)
