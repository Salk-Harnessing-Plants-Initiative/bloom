"""Tests for scripts/lint_migration_isolation.py.

A PR with a migration change may only change files in the migration surface. These run
the script against scratch git repos laid out like the real branch model: main, staging
ahead of it, and feature or hotfix branches off either.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "lint_migration_isolation.py"
MAKEFILE = REPO_ROOT / "Makefile"

M1 = "supabase/migrations/20260101000000_one.sql"
M2 = "supabase/migrations/20260102000000_two.sql"
M3 = "supabase/migrations/20260103000000_three.sql"
NOT_ISOLATED = "::error title=Migration change not isolated::"


def _load():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("lint_migration_isolation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """main has M1; staging adds M2 plus application code, as a real staging does."""
    _git(tmp_path, "init", "-q", "--initial-branch=main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    _write(tmp_path, M1, "SELECT 1;\n")
    _commit(tmp_path, "m1")
    _git(tmp_path, "checkout", "-q", "-b", "staging")
    _write(tmp_path, M2, "SELECT 2;\n")
    _write(tmp_path, "web/app/page.tsx")
    _commit(tmp_path, "staging work")
    return tmp_path


def _feature(repo: Path, *paths: str, base: str = "staging", name: str = "feature") -> None:
    _git(repo, "checkout", "-q", "-b", name, base)
    for rel in paths:
        _write(repo, rel)
    _commit(repo, name)


def _lint(repo: Path, base: str, head: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), base, "--head", head, "--staging-ref", "staging", "--repo", str(repo)],
        capture_output=True,
        text=True,
    )


SURFACE_PATHS = [
    "supabase/rollbacks/20260103000000_three_rollback.sql",
    "supabase/grants/schema_grants.sql",
    "tests/integration/test_three.py",
    "tests/unit/test_three.py",
    "web/lib/database.types.ts",
    "web/types/database.types.ts",
    "packages/bloom-js/src/types/database.types.ts",
    "packages/bloom-fs/src/types/database.types.ts",
    "packages/bloom-nextjs-auth/src/lib/database.types.ts",
    "_WIKI/SUPABASE/ERD/README.md",
    "docs/plan/tasks.md",
    "services/workflows/README.md",
]


@pytest.mark.parametrize("path", SURFACE_PATHS)
def test_migration_with_a_surface_file_passes(repo, path):
    _feature(repo, M3, path)
    result = _lint(repo, "staging", "feature")
    assert result.returncode == 0, result.stdout


def test_migration_with_application_code_fails_naming_the_file(repo):
    _feature(repo, M3, "web/app/x/page.tsx")
    result = _lint(repo, "staging", "feature")
    assert result.returncode == 1
    assert NOT_ISOLATED in result.stdout
    assert "web/app/x/page.tsx" in result.stdout


def test_rename_out_of_application_code_flags_the_source(repo):
    _feature(repo, "web/a.ts", name="seed")
    _git(repo, "checkout", "-q", "-b", "feature")
    _write(repo, M3)
    (repo / "tests" / "unit").mkdir(parents=True)
    _git(repo, "mv", "web/a.ts", "tests/unit/a.ts")
    _commit(repo, "move")
    result = _lint(repo, "seed", "feature")
    assert result.returncode == 1
    assert "web/a.ts" in result.stdout


@pytest.mark.parametrize("path", ["tests/unit_x/helper.py", "_WIKI2/notes.txt", "supabase/migrations_old/x.sql"])
def test_look_alike_prefixes_are_not_the_surface(repo, path):
    _feature(repo, M3, path)
    result = _lint(repo, "staging", "feature")
    assert result.returncode == 1
    assert path in result.stdout


def test_pr_without_a_migration_change_is_not_flagged(repo):
    _feature(repo, "web/app/x/page.tsx")
    result = _lint(repo, "staging", "feature")
    assert result.returncode == 0


def test_promotion_from_staging_passes(repo):
    result = _lint(repo, "main", "staging")
    assert result.returncode == 0, result.stdout


def test_hotfix_to_main_with_application_code_fails(repo):
    _feature(repo, "supabase/migrations/20260104000000_hotfix.sql", "web/app/x/page.tsx", base="main", name="hotfix")
    result = _lint(repo, "main", "hotfix")
    assert result.returncode == 1
    assert "web/app/x/page.tsx" in result.stdout


def test_unresolvable_base_exits_2(repo):
    result = _lint(repo, "no-such-ref", "staging")
    assert result.returncode == 2
    assert "::error title=lint_migration_isolation: cannot resolve ref::" in result.stdout


def test_type_files_follow_make_gen_types():
    """The allow-list names exactly what `make gen-types` writes, plus the hand-kept web/types copy."""
    recipe = re.search(r"^gen-types:.*?(?=^\S|\Z)", MAKEFILE.read_text(encoding="utf-8"), re.M | re.S)
    assert recipe, "Makefile has no gen-types target"
    targets = set(re.findall(r"cp /tmp/database\.types\.ts (\S+)", recipe.group(0)))
    assert targets, "gen-types copies no database.types.ts files"
    module = _load()
    assert set(module.TYPE_FILES) == targets | {"web/types/database.types.ts"}
