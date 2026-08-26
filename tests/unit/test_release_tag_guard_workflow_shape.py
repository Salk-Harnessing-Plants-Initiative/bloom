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

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
GUARD = WORKFLOWS / "release-tag-guard.yml"


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
    job = _load(GUARD)["jobs"]["check-tag-matches-a-known-package"]
    assert job.get("permissions", {}).get("id-token") != "write"
    assert "environment" not in job


def _run_guard_script(tag: str) -> subprocess.CompletedProcess:
    job = _load(GUARD)["jobs"]["check-tag-matches-a-known-package"]
    (step,) = [s for s in job["steps"] if s.get("run")]
    return subprocess.run(
        ["bash", "-c", step["run"]],
        env={"TAG": tag},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "tag",
    ["bloomctl-v0.1.0a1", "bloommcp-v0.1.0a1", "bloomctl-v1.0.0", "bloommcp-v1.0.0"],
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
