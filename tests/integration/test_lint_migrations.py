"""
Tests for scripts/lint_migrations.sh.

Creates scratch git repos with synthetic migration layouts and runs the
lint script against them. The lint grandfathers files present on BASE_REF
and only flags files added after that ref.
"""

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent
LINT_SCRIPT = REPO_ROOT / "scripts" / "lint_migrations.sh"


def _run(cmd, cwd, env=None, check=True):
    """Run a shell command; return CompletedProcess. `check=False` to let the caller inspect a non-zero exit."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=full_env,
        capture_output=True,
        text=True,
        check=check,
    )


def _init_repo(root: Path) -> None:
    """Initialize an empty git repo with deterministic author config."""
    root.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--initial-branch=main"], cwd=root)
    _run(["git", "config", "user.email", "test@example.com"], cwd=root)
    _run(["git", "config", "user.name", "Test"], cwd=root)


def _commit_file(root: Path, relative: str, content: str, message: str) -> None:
    """Write a file, git add, git commit."""
    full = root / relative
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    _run(["git", "add", relative], cwd=root)
    _run(["git", "commit", "-m", message], cwd=root)


def _set_up_base(root: Path) -> None:
    """Initialize a repo with a 'main' branch containing one valid migration."""
    _init_repo(root)
    _commit_file(
        root,
        "supabase/migrations/20250101000000_initial.sql",
        "SELECT 1;\n",
        "seed migration on main",
    )
    # Copy the lint script into the scratch repo's `scripts/` so the test
    # doesn't depend on the path-to-cwd relationship.
    dest = root / "scripts" / "lint_migrations.sh"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(LINT_SCRIPT.read_text())
    dest.chmod(0o755)
    _run(["git", "add", "scripts/lint_migrations.sh"], cwd=root)
    _run(["git", "commit", "-m", "add lint script"], cwd=root)


def _add_feature_branch_with(root: Path, relative: str, content: str) -> None:
    """Switch to a feature branch and add one new migration."""
    _run(["git", "checkout", "-b", "feature"], cwd=root)
    _commit_file(root, relative, content, f"add {Path(relative).name}")


def _run_lint(root: Path, base_ref: str = "main"):
    """Invoke the lint script with the given base ref."""
    return _run(
        ["bash", "./scripts/lint_migrations.sh", base_ref],
        cwd=root,
        check=False,
    )


# -----------------------------------------------------------------------------


def test_valid_new_migration_passes(tmp_path):
    """A well-formed new migration with a future timestamp passes the lint."""
    _set_up_base(tmp_path)
    _add_feature_branch_with(
        tmp_path,
        "supabase/migrations/20260420010000_add_foo.sql",
        "SELECT 2;\n",
    )

    result = _run_lint(tmp_path, base_ref="main")

    assert result.returncode == 0, (
        f"Expected exit 0 for valid migration; got {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "Migration lint passed" in result.stdout


def test_malformed_new_filename_fails(tmp_path):
    """Missing HHMMSS segment → lint fails with Invalid migration filename annotation."""
    _set_up_base(tmp_path)
    _add_feature_branch_with(
        tmp_path,
        "supabase/migrations/20260420_missing_hms.sql",
        "SELECT 3;\n",
    )

    result = _run_lint(tmp_path, base_ref="main")

    assert result.returncode == 1
    assert "Invalid migration filename" in (result.stdout + result.stderr)
    assert "20260420_missing_hms.sql" in (result.stdout + result.stderr)


def test_stale_timestamp_fails(tmp_path):
    """A new migration with a timestamp <= max on base_ref fails."""
    _set_up_base(tmp_path)
    # main already has 20250101000000_initial.sql; feature branch adds an older one.
    _add_feature_branch_with(
        tmp_path,
        "supabase/migrations/20200101000000_stale.sql",
        "SELECT 4;\n",
    )

    result = _run_lint(tmp_path, base_ref="main")

    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "Stale migration timestamp" in combined
    assert "20200101000000_stale.sql" in combined


def test_grandfathered_historical_file_passes(tmp_path):
    """
    A file already on main with a hyphen in its name (which would fail the
    strict pattern for a new file) must NOT be flagged when touching unrelated
    files on a feature branch. Only files added in the PR diff are linted.
    """
    _set_up_base(tmp_path)
    # Commit a weirdly-named migration directly onto main (grandfathered).
    _commit_file(
        tmp_path,
        "supabase/migrations/20250617163449_insert_image_2.0_rpc.sql",
        "SELECT 5;\n",
        "add historical file with dots",
    )
    # Feature branch adds an unrelated, valid migration.
    _add_feature_branch_with(
        tmp_path,
        "supabase/migrations/20260420010000_add_foo.sql",
        "SELECT 6;\n",
    )

    result = _run_lint(tmp_path, base_ref="main")

    assert result.returncode == 0, (
        f"Historical file with dots should have been grandfathered; "
        f"lint exit {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_no_new_files_on_feature_branch_passes(tmp_path):
    """
    If the PR adds no new migrations at all, lint passes trivially — even if
    historical files on main have non-conforming names.
    """
    _set_up_base(tmp_path)
    _commit_file(
        tmp_path,
        "supabase/migrations/20250617163449_insert_image_2.0_rpc.sql",
        "SELECT 7;\n",
        "add historical file with dots",
    )
    # Switch to a feature branch and don't touch that file at all.
    _run(["git", "checkout", "-b", "feature"], cwd=tmp_path)

    result = _run_lint(tmp_path, base_ref="main")

    assert result.returncode == 0, (
        f"No new files on feature branch; lint should pass. "
        f"Got exit {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_missing_base_ref_fails_fast(tmp_path):
    """
    If BASE_REF is set to a remote ref that can't be fetched, the lint must
    fail fast rather than silently falling back to baseline=0 (which would
    let every new migration pass trivially).
    """
    _init_repo(tmp_path)
    _commit_file(
        tmp_path,
        "supabase/migrations/20260420010000_foo.sql",
        "SELECT 8;\n",
        "add foo",
    )
    # Copy the script
    dest = tmp_path / "scripts" / "lint_migrations.sh"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(LINT_SCRIPT.read_text())
    dest.chmod(0o755)

    # origin/nonexistent — we set no remote, so the fetch will fail.
    result = _run_lint(tmp_path, base_ref="origin/nonexistent-branch")

    assert result.returncode == 2, (
        f"Expected exit 2 for unreachable base ref; got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
