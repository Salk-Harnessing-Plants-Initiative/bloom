"""
Tests for scripts/promote_security_to_main.sh.

Builds a scratch `origin` remote with `main` and `staging` branches carrying a
mix of commit kinds, then runs the promoter against a working clone. Verifies:
  - which commits get auto-selected (pure CVE-surface + security intent),
  - which are routed to manual review (non-surface security changes),
  - which are ignored (feature/DB work, non-security dep bumps),
  - the threshold gate, and the built branch's contents.
"""

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent
PROMO_SCRIPT = REPO_ROOT / "scripts" / "promote_security_to_main.sh"


def _run(cmd, cwd, env=None, check=True):
    full_env = os.environ.copy()
    # Keep git deterministic and quiet regardless of the host user's config.
    full_env.setdefault("GIT_AUTHOR_NAME", "Test")
    full_env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    full_env.setdefault("GIT_COMMITTER_NAME", "Test")
    full_env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd, cwd=cwd, env=full_env, capture_output=True, text=True, check=check
    )


def _git(root, *args, check=True):
    return _run(["git", *args], cwd=root, check=check)


def _write(root: Path, rel: str, content: str) -> None:
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def _commit(root: Path, files: dict, message: str) -> str:
    for rel, content in files.items():
        _write(root, rel, content)
        _git(root, "add", rel)
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "--short", "HEAD").stdout.strip()


@pytest.fixture
def stack(tmp_path):
    """A working clone whose `origin` has main + staging.

    main: seed files. staging: main plus a curated set of commits. Returns
    (work_dir, dict of labelled short-SHAs) and installs the script locally.
    """
    origin = tmp_path / "origin.git"
    _run(["git", "init", "--bare", "--initial-branch=main", str(origin)], cwd=tmp_path)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    _git(seed, "remote", "add", "origin", str(origin))
    _commit(
        seed,
        {
            ".trivyignore": "# seed\n",
            "bloommcp/Dockerfile": "FROM python:3.11-slim\n",
            "langchain/uv.lock": "# lock v0\n",
            "web/app/page.tsx": "export default function P(){}\n",
            "bloommcp/pyproject.toml": "[project]\nname='x'\n",
            "supabase/migrations/0001.sql": "-- seed\n",
        },
        "seed main",
    )
    _git(seed, "push", "origin", "main")

    # staging carries the mix of commit kinds we want to classify.
    _git(seed, "checkout", "-b", "staging")
    shas = {}
    shas["trivy_only"] = _commit(
        seed, {".trivyignore": "# seed\nCVE-2026-0001\n"}, "ci: ignore CVE-2026-0001"
    )
    shas["dockerfile_pin"] = _commit(
        seed,
        {"web/Dockerfile.bloom-web.prod": "FROM node:20\nRUN npm i -g npm@11\n"},
        "fix(security): pin npm in web runner",
    )
    shas["lockfile_bump"] = _commit(
        seed, {"langchain/uv.lock": "# lock v1\n"}, "chore(deps): bump langchain for CVE"
    )
    shas["mixed_feature"] = _commit(
        seed,
        {".trivyignore": "# seed\nCVE-2026-0001\nCVE-2026-0002\n", "web/app/page.tsx": "export default function P(){return 1}\n"},
        "feat(db): add thing and suppress a CVE",
    )
    shas["security_code"] = _commit(
        seed,
        {"supabase/migrations/0002.sql": "REVOKE ALL ON x;\n"},
        "fix(security): scope down GRANT on admin",
    )
    shas["manifest_bump"] = _commit(
        seed,
        {"bloommcp/pyproject.toml": "[project]\nname='x'\nversion='2'\n", "langchain/uv.lock": "# lock v2\n"},
        "chore(deps): bump cryptography past CVE",
    )
    shas["plain_feature"] = _commit(
        seed, {"web/app/page.tsx": "export default function P(){return 2}\n"}, "feat: unrelated feature"
    )
    shas["plain_dep"] = _commit(
        seed, {"langchain/uv.lock": "# lock v3\n"}, "chore(deps): add a new library"
    )
    _git(seed, "push", "origin", "staging")

    work = tmp_path / "work"
    _run(["git", "clone", str(origin), str(work)], cwd=tmp_path)
    _git(work, "checkout", "main")
    dest = work / "scripts" / "promote_security_to_main.sh"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(PROMO_SCRIPT.read_text())
    dest.chmod(0o755)

    return work, shas


def _promote(work, *args, env=None):
    base = {"PROMO_DATE": "test", "PROMO_BRANCH": "promote/security-to-main",
            "PR_BODY_FILE": str(work / "body.md")}
    if env:
        base.update(env)
    return _run(["bash", "./scripts/promote_security_to_main.sh", *args], cwd=work,
                env=base, check=False)


def _sections(out: str) -> dict:
    """Split dry-run output into {'auto': [shas], 'manual': [shas]} by header."""
    result = {"auto": [], "manual": []}
    current = None
    for line in out.splitlines():
        if line.startswith("---- auto-promotable"):
            current = "auto"
        elif line.startswith("---- needs manual review"):
            current = "manual"
        elif line.strip() == "----":
            current = None
        elif current and line.strip():
            result[current].append(line.split()[0])
    return result


# -----------------------------------------------------------------------------


def test_pure_surface_security_selected(stack):
    work, shas = stack
    sec = _sections(_promote(work, "--dry-run").stdout)
    for key in ("trivy_only", "dockerfile_pin", "lockfile_bump"):
        assert shas[key] in sec["auto"], f"{key} should be auto-promotable\n{sec}"


def test_non_surface_security_goes_manual(stack):
    work, shas = stack
    sec = _sections(_promote(work, "--dry-run").stdout)
    assert shas["security_code"] in sec["manual"], sec
    assert shas["manifest_bump"] in sec["manual"], sec


def test_mixed_and_plain_commits_excluded(stack):
    work, shas = stack
    out = _promote(work, "--dry-run").stdout
    # A feature commit that merely grazed .trivyignore is not security work.
    assert shas["mixed_feature"] not in out, out
    # Non-security feature + non-CVE dependency add are ignored entirely.
    assert shas["plain_feature"] not in out, out
    assert shas["plain_dep"] not in out, out


def test_below_threshold_exits_3(stack):
    work, _ = stack
    # 3 auto-promotable commits; require > 5.
    result = _promote(work, "--min", "5")
    assert result.returncode == 3, result.stdout + result.stderr
    assert "Below threshold" in result.stdout


def test_builds_branch_above_threshold(stack):
    work, _ = stack
    result = _promote(work, "--min", "2")
    assert result.returncode == 0, result.stdout + result.stderr
    # Cherry-picking rewrites SHAs, so match by subject on the built branch.
    log = _git(work, "log", "--format=%s", "origin/main..HEAD").stdout
    for subject in ("ci: ignore CVE-2026-0001", "fix(security): pin npm in web runner",
                    "chore(deps): bump langchain for CVE"):
        assert subject in log, f"{subject!r} missing from built branch\n{log}"


def test_built_branch_only_touches_cve_surface(stack):
    work, _ = stack
    assert _promote(work, "--min", "2").returncode == 0
    changed = _git(work, "diff", "--name-only", "origin/main..HEAD").stdout.split()
    allowed_base = {"Dockerfile", "package-lock.json", "uv.lock", "poetry.lock"}
    for f in changed:
        name = Path(f).name
        ok = (name == ".trivyignore" or name in allowed_base
              or name.endswith(".Dockerfile") or name.startswith("Dockerfile."))
        assert ok, f"{f} is outside the CVE-fix surface\n{changed}"


def test_pr_body_lists_both_buckets(stack):
    work, _ = stack
    assert _promote(work, "--min", "2").returncode == 0
    body = (work / "body.md").read_text()
    assert "### Promoted" in body
    assert "Needs manual promotion" in body


def test_already_in_base_is_skipped(stack):
    work, shas = stack
    # Cherry-pick the trivy_only commit onto main and push, so it is already there.
    _git(work, "cherry-pick", shas["trivy_only"])
    _git(work, "push", "origin", "HEAD:main")
    _git(work, "checkout", "main")
    _git(work, "reset", "--hard", "origin/main")
    result = _promote(work, "--min", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    log = _git(work, "log", "--oneline", "origin/main..HEAD").stdout
    # The already-present commit's subject must not be re-added.
    assert "ignore CVE-2026-0001" not in log, log


def test_cherry_pick_conflict_is_flagged(tmp_path):
    """A selected commit that conflicts on a diverged file is flagged, not fatal.

    main diverges from staging on a shared lockfile line, so cherry-picking the
    staging security bump for that line conflicts. The clean security commits
    must still promote; the conflicting one lands in the PR body's manual bucket.
    """
    origin = tmp_path / "origin.git"
    _run(["git", "init", "--bare", "--initial-branch=main", str(origin)], cwd=tmp_path)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    _git(seed, "remote", "add", "origin", str(origin))
    _commit(seed, {"langchain/uv.lock": "# lock\nshared = 0\n", ".trivyignore": "# seed\n"}, "seed main")
    _git(seed, "push", "origin", "main")

    # staging: two clean security commits + one that edits the shared lock line.
    _git(seed, "checkout", "-b", "staging")
    _commit(seed, {".trivyignore": "# seed\nCVE-1\n"}, "ci(security): ignore CVE-1")
    _commit(seed, {"web/Dockerfile.prod": "FROM node:20\n"}, "fix(security): pin node base")
    _commit(seed, {"langchain/uv.lock": "# lock\nshared = 111\n"}, "chore(deps): bump for CVE on shared line")
    _git(seed, "push", "origin", "staging")

    # main independently edits the SAME lock line (not on staging) -> conflict.
    _git(seed, "checkout", "main")
    _commit(seed, {"langchain/uv.lock": "# lock\nshared = 999\n"}, "chore: main-only lock change")
    _git(seed, "push", "origin", "main")

    work = tmp_path / "work"
    _run(["git", "clone", str(origin), str(work)], cwd=tmp_path)
    _git(work, "checkout", "main")
    dest = work / "scripts" / "promote_security_to_main.sh"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(PROMO_SCRIPT.read_text())
    dest.chmod(0o755)

    result = _promote(work, "--min", "1")
    assert result.returncode == 0, result.stdout + result.stderr

    # Clean picks promoted; the conflicting lockfile bump is NOT on the branch.
    log = _git(work, "log", "--format=%s", "origin/main..HEAD").stdout
    assert "ci(security): ignore CVE-1" in log, log
    assert "fix(security): pin node base" in log, log
    assert "bump for CVE on shared line" not in log, log

    # The branch is clean (not left mid-pick).
    assert not _git(work, "diff", "--name-only", "--diff-filter=U").stdout.strip()

    # PR body flags the conflict for manual promotion, naming the file.
    body = (work / "body.md").read_text()
    assert "Cherry-pick conflicts" in body, body
    assert "langchain/uv.lock" in body, body
