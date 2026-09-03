"""Regression guard for release-tag-guard.yml (#663 review).

release-bloomcli.yml and release-bloommcp.yml each skip cleanly (not fail)
when a Release tag belongs to the *other* package — the expected, silent
outcome. But a typo'd or unknown-prefix tag (e.g. `bloomcp-v1.0.0`) makes
BOTH skip at once, with no failing run anywhere to say nothing shipped.
release-tag-guard.yml closes that gap: it never skips on a release event and
fails loudly when a tag matches neither known prefix. This workflow can never
be exercised by normal PR CI (it only fires on a published Release), so this
unit test is the only pre-merge gate on its shape.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
GUARD = WORKFLOWS / "release-tag-guard.yml"
RELEASE_BLOOMCLI = WORKFLOWS / "release-bloomcli.yml"
RELEASE_BLOOMMCP = WORKFLOWS / "release-bloommcp.yml"

# See test_check_kong_restart_delta_script.py's / test_deploy_kong_reload_on_config_change.py's
# identical helper for why this is needed: `bash` can resolve to the WSL launcher shim rather
# than a real POSIX shell on some Windows dev machines, depending on which process's PATH is
# being searched.
_GIT_BASH_CANDIDATES = [
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
]


def _bash_executable() -> str:
    for candidate in _GIT_BASH_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return shutil.which("bash") or "bash"


BASH = _bash_executable()


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    # PyYAML parses the bare key `on` as boolean True (YAML 1.1).
    return wf.get("on") or wf.get(True)


def test_triggers_only_on_release_and_never_skips():
    wf = _load(GUARD)
    on = _on(wf)
    assert set(on) == {"release"}
    assert on["release"]["types"] == ["published"]

    job = wf["jobs"]["check-tag-matches-a-known-package"]
    assert "if" not in job, "this workflow must never skip on a release event"


def test_holds_no_publish_credential():
    wf = _load(GUARD)
    job = wf["jobs"]["check-tag-matches-a-known-package"]
    assert job.get("permissions", {}).get("id-token") != "write"
    assert "environment" not in job
    # This job never checks out the repo or calls the GitHub API — strictly
    # minimal permissions, not just "no publish credential".
    assert wf.get("permissions") == {}


def _run_guard_script(tag: str) -> subprocess.CompletedProcess:
    job = _load(GUARD)["jobs"]["check-tag-matches-a-known-package"]
    (step,) = [s for s in job["steps"] if s.get("run")]
    # Merge with os.environ (not replace it): dropping PATH here would make
    # `bash` itself fail to resolve `shopt`/`case` builtins' surrounding
    # shell on some platforms.
    env = {**os.environ, "TAG": tag}
    return subprocess.run(
        [BASH, "-c", step["run"]],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    "tag",
    [
        "bloomctl-v0.1.0a1",
        "bloommcp-v0.1.0a1",
        "bloomctl-v1.0.0",
        "bloommcp-v1.0.0",
        # GitHub Actions' startsWith() (used by the real per-package guards)
        # is case-insensitive; this guard's bash `case` match must agree,
        # or it misreports a tag that actually matched a known package.
        "BLOOMMCP-v1.0.0",
        "Bloomctl-V1.0.0",
    ],
)
def test_known_package_tags_pass(tag):
    result = _run_guard_script(tag)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "tag",
    ["bloomcp-v0.1.0a1", "bloom-mcp-v0.1.0a1", "v1.0.0", "random-tag", ""],
)
def test_unknown_prefix_tags_fail_loudly(tag):
    result = _run_guard_script(tag)
    assert result.returncode != 0
    assert "::error::" in result.stdout


def _release_workflow_prefix(path: Path) -> str:
    condition = _load(path)["jobs"]["validate-release"].get("if", "")
    match = re.search(r"startsWith\(github\.event\.release\.tag_name, '([^']+)'\)", condition)
    assert match, f"could not find a startsWith(...) tag-prefix guard in {path.name}"
    return match.group(1)


def _guard_known_prefixes() -> set[str]:
    job = _load(GUARD)["jobs"]["check-tag-matches-a-known-package"]
    (step,) = [s for s in job["steps"] if s.get("run")]
    match = re.search(r"for prefix in ([^;]+);", step["run"])
    assert match, "could not find the `for prefix in ...` line in release-tag-guard.yml"
    return set(match.group(1).split())


def test_guard_prefixes_match_every_release_workflows_own_guard():
    """Nothing else enforces KNOWN_PREFIXES stays in sync with each package's
    own tag-prefix guard (see the module docstring's comment). If a package is
    added or renamed and only its own release-*.yml is updated, this fails in
    CI immediately instead of the omission only surfacing once that package's
    release tag is actually cut in production."""
    assert _guard_known_prefixes() == {
        _release_workflow_prefix(RELEASE_BLOOMCLI),
        _release_workflow_prefix(RELEASE_BLOOMMCP),
    }


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
