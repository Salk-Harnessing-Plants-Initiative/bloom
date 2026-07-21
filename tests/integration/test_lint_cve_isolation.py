"""
Tests for scripts/lint_cve_isolation.sh.

Creates scratch git repos with a base 'main' branch and a 'feature' branch
carrying a set of changes, then runs the lint against them. The lint only
fires when `.trivyignore` is among the changed files; alongside it, only the
CVE-fix surface (Dockerfiles, lockfiles) is permitted.
"""

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent.parent
LINT_SCRIPT = REPO_ROOT / "scripts" / "lint_cve_isolation.sh"


def _run(cmd, cwd, env=None, check=True):
    """Run a shell command; return CompletedProcess. `check=False` to inspect a non-zero exit."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd, cwd=cwd, env=full_env, capture_output=True, text=True, check=check
    )


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--initial-branch=main"], cwd=root)
    _run(["git", "config", "user.email", "test@example.com"], cwd=root)
    _run(["git", "config", "user.name", "Test"], cwd=root)


def _commit_file(root: Path, relative: str, content: str, message: str) -> None:
    full = root / relative
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    _run(["git", "add", relative], cwd=root)
    _run(["git", "commit", "-m", message], cwd=root)


def _set_up_base(root: Path) -> None:
    """A 'main' branch with a seed .trivyignore, a Dockerfile, a lockfile, and app code."""
    _init_repo(root)
    _commit_file(root, ".trivyignore", "# seed\nCVE-0000-0000\n", "seed trivyignore")
    _commit_file(root, "bloommcp/Dockerfile", "FROM python:3.11-slim\n", "seed dockerfile")
    _commit_file(root, "uv.lock", "# lock v1\n", "seed lockfile")
    _commit_file(root, "web/app/page.tsx", "export default function P(){}\n", "seed app code")
    # Copy the lint script in so the test is independent of cwd.
    dest = root / "scripts" / "lint_cve_isolation.sh"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(LINT_SCRIPT.read_text())
    dest.chmod(0o755)
    _run(["git", "add", "scripts/lint_cve_isolation.sh"], cwd=root)
    _run(["git", "commit", "-m", "add lint script"], cwd=root)


def _feature_branch(root: Path) -> None:
    _run(["git", "checkout", "-b", "feature"], cwd=root)


def _modify(root: Path, relative: str, content: str) -> None:
    full = root / relative
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    _run(["git", "add", relative], cwd=root)


def _commit(root: Path, message: str) -> None:
    _run(["git", "commit", "-m", message], cwd=root)


def _run_lint(root: Path, base_ref: str = "main"):
    return _run(["bash", "./scripts/lint_cve_isolation.sh", base_ref], cwd=root, check=False)


# -----------------------------------------------------------------------------


def test_trivyignore_only_passes(tmp_path):
    """A PR that changes only .trivyignore is allowed."""
    _set_up_base(tmp_path)
    _feature_branch(tmp_path)
    _modify(tmp_path, ".trivyignore", "# seed\nCVE-0000-0000\nCVE-2026-53260\n")
    _commit(tmp_path, "suppress CVE")
    result = _run_lint(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_trivyignore_with_dockerfile_and_lockfile_passes(tmp_path):
    """.trivyignore alongside a base-image bump + lockfile (the CVE-fix surface) is allowed."""
    _set_up_base(tmp_path)
    _feature_branch(tmp_path)
    _modify(tmp_path, ".trivyignore", "# seed\n")  # entry removed after a bump
    _modify(tmp_path, "bloommcp/Dockerfile", "FROM python:3.11-slim@sha256:newer\n")
    _modify(tmp_path, "uv.lock", "# lock v2\n")
    _commit(tmp_path, "bump base image, drop suppression")
    result = _run_lint(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_trivyignore_with_app_code_fails(tmp_path):
    """.trivyignore folded into an unrelated app change is rejected."""
    _set_up_base(tmp_path)
    _feature_branch(tmp_path)
    _modify(tmp_path, ".trivyignore", "# seed\nCVE-2026-53260\n")
    _modify(tmp_path, "web/app/page.tsx", "export default function P(){return null}\n")
    _commit(tmp_path, "feature + sneaky suppression")
    result = _run_lint(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "web/app/page.tsx" in result.stdout


def test_no_trivyignore_change_passes(tmp_path):
    """A PR that never touches .trivyignore is not flagged, regardless of files changed."""
    _set_up_base(tmp_path)
    _feature_branch(tmp_path)
    _modify(tmp_path, "web/app/page.tsx", "export default function P(){return null}\n")
    _modify(tmp_path, "bloommcp/Dockerfile", "FROM python:3.11-slim@sha256:other\n")
    _commit(tmp_path, "normal feature work")
    result = _run_lint(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_base_ref_errors(tmp_path):
    """An unreachable origin/* base ref fails loudly (exit 2), never a silent pass."""
    _set_up_base(tmp_path)
    _feature_branch(tmp_path)
    _modify(tmp_path, ".trivyignore", "# seed\nCVE-2026-53260\n")
    _commit(tmp_path, "suppress CVE")
    result = _run_lint(tmp_path, base_ref="origin/does-not-exist")
    assert result.returncode == 2, result.stdout + result.stderr
