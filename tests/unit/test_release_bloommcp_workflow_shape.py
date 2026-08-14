"""Regression guard for the bloommcp release + version workflows.

Mirrors tests/unit/test_release_bloomcli_workflow_shape.py — these workflows can
never be exercised by PR CI before they land (release-bloommcp.yml fires only on
a published Release or manual dispatch; version-bloommcp.yml is dispatch-only),
so this unit test is the only pre-merge gate on their shape.

It locks the safety-critical properties the design (openspec
add-bloommcp-pypi-release-pipeline) signed off on:
  - the publish workflow triggers ONLY on a Release (`published`) or
    workflow_dispatch — never on a push or tag;
  - `validate-release` skips cleanly (job-level `if:`, not a step) for a
    Release tag that isn't bloommcp's own, while workflow_dispatch always
    passes the guard;
  - `build-and-publish` is gated by `needs: validate-release`;
  - `build-and-publish` requests the OIDC token (`id-token: write`) and pins the
    `pypi` environment so trusted publishing works, and stores no API token;
  - the actual `uv publish` runs only on a real Release event;
  - the built wheel is smoke-tested (import of bloom_mcp plus the concrete
    Supabase adapters, and `bloom-mcp --version`) before upload;
  - there is no TestPyPI lane;
  - the version workflow bumps via `uv version`, syncs `uv.lock`, and opens a PR.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release-bloommcp.yml"
VERSION = WORKFLOWS / "version-bloommcp.yml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    # PyYAML parses the bare key `on` as boolean True (YAML 1.1).
    return wf.get("on") or wf.get(True)


def _steps_text(job: dict) -> str:
    parts = []
    for s in job["steps"]:
        parts.append(str(s.get("run", "")))
        parts.append(str(s.get("uses", "")))
        parts.append(str(s.get("env", "")))  # untrusted inputs are passed via env:
    return "\n".join(parts)


def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- publish workflow: triggers --------------------------------------------

def test_release_triggers_only_on_release_and_dispatch():
    on = _on(_load(RELEASE))
    assert set(on) == {"release", "workflow_dispatch"}
    assert on["release"]["types"] == ["published"]
    assert "push" not in on


# --- publish workflow: skips cleanly for a different package's release -----

def test_validate_release_skips_tags_that_are_not_bloommcps():
    """A bloomctl release must not produce a failing run here (#663)."""
    job = _load(RELEASE)["jobs"]["validate-release"]
    condition = job.get("if", "")
    assert "startsWith(github.event.release.tag_name, 'bloommcp-')" in condition
    assert "github.event_name != 'release'" in condition


# --- publish workflow: validate gates publish ------------------------------

def test_publish_needs_validate_release():
    jobs = _load(RELEASE)["jobs"]
    assert "validate-release" in jobs
    assert "build-and-publish" in jobs
    assert jobs["build-and-publish"]["needs"] == "validate-release"


def test_validate_checks_tag_changelog_lint_tests():
    text = _steps_text(_load(RELEASE)["jobs"]["validate-release"])
    assert "github.event.release.tag_name" in text  # tag <-> version match
    assert "CHANGELOG.md" in text                    # changelog entry check
    assert "ruff" in text                            # lint
    assert "pytest" in text                          # tests


# --- publish workflow: trusted publishing + immutability guard -------------

def test_publish_uses_oidc_pypi_env_and_no_token():
    wf = _load(RELEASE)
    job = wf["jobs"]["build-and-publish"]
    assert job["permissions"]["id-token"] == "write"
    assert job["environment"] == "pypi"
    text = _steps_text(job)
    assert "uv publish --trusted-publishing always" in text
    raw = _raw(RELEASE)
    assert "PYPI_API_TOKEN" not in raw
    assert "test.pypi.org" not in raw  # no TestPyPI lane


def test_publish_step_gated_on_real_release():
    job = _load(RELEASE)["jobs"]["build-and-publish"]
    publish = [s for s in job["steps"] if "uv publish" in str(s.get("run", ""))]
    assert len(publish) == 1
    assert publish[0]["if"] == "github.event_name == 'release'"


def test_built_wheel_is_smoke_tested_before_publish():
    text = _steps_text(_load(RELEASE)["jobs"]["build-and-publish"])
    assert "uv build" in text
    assert "import bloom_mcp" in text
    assert "bloom-mcp --version" in text
    assert "dist/*.whl" in text


def test_the_wheel_gate_imports_the_concrete_supabase_adapters():
    """build_app() alone doesn't reach these — they're wired by main()'s
    composition root, after the --version early return. Explicit imports here
    close the class of gap bloomcli's #629 exploited.
    """
    text = _steps_text(_load(RELEASE)["jobs"]["build-and-publish"])
    assert "SupabaseReader" in text
    assert "SupabaseResultStore" in text
    assert "from postgrest import APIError" in text
    assert "from supabase import create_client" in text


# --- version workflow -------------------------------------------------------

def test_version_workflow_is_dispatch_only_with_bump_input():
    on = _on(_load(VERSION))
    assert set(on) == {"workflow_dispatch"}
    inputs = on["workflow_dispatch"]["inputs"]
    assert "bump_type" in inputs
    assert {"patch", "minor", "major"}.issubset(set(inputs["bump_type"]["options"]))


def test_version_workflow_bumps_syncs_lock_and_opens_pr():
    wf = _load(VERSION)
    assert wf["permissions"]["contents"] == "write"
    assert wf["permissions"]["pull-requests"] == "write"
    text = _steps_text(wf["jobs"]["bump-version"])
    assert "uv version" in text
    assert "uv lock" in text  # bloommcp/uv.lock must stay in sync with the bump
    assert "peter-evans/create-pull-request" in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
