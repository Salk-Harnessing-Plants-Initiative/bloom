"""Regression guard for the bloomcli publish + version-bump workflows.

These three workflows can never be exercised by PR CI before they land
(release-* fire only on push to staging/main or a tag; the version-bump check
gates merges), so this unit test is the only pre-merge gate on their shape.

It locks the safety-critical properties the design signed off on:
  - dev builds go to TestPyPI, final builds go to real PyPI;
  - both publish jobs request the OIDC token (`id-token: write`) and pin an
    environment so PyPI trusted publishing works;
  - the prod job is immutability-safe (skips when the version already exists);
  - prod fires on BOTH a main push and a bloomcli-v* tag, plus manual dispatch;
  - the version-bump check runs on PRs to staging AND main and inspects
    bloomcli/src.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
DEV = WORKFLOWS / "release-bloomcli-dev.yml"
PROD = WORKFLOWS / "release-bloomcli.yml"
BUMP = WORKFLOWS / "bloomcli-version-bump-check.yml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    # PyYAML parses the bare key `on` as boolean True (YAML 1.1).
    return wf.get("on") or wf.get(True)


def _steps_text(job: dict) -> str:
    return "\n".join(str(s.get("run", "")) for s in job["steps"])


# --- dev → TestPyPI ---------------------------------------------------------

def test_dev_triggers_on_staging_push_and_dispatch():
    on = _on(_load(DEV))
    assert "workflow_dispatch" in on
    assert on["push"]["branches"] == ["staging"]
    assert any("bloomcli/**" in p for p in on["push"]["paths"])


def test_dev_publishes_to_testpypi_with_oidc():
    wf = _load(DEV)
    assert wf["permissions"]["id-token"] == "write"
    job = wf["jobs"]["testpypi"]
    assert job["environment"] == "testpypi"
    text = _steps_text(job)
    assert "test.pypi.org/legacy/" in text
    assert "--trusted-publishing always" in text
    assert "uv build" in text


def test_dev_stamps_a_dev_version():
    assert "dev${{ github.run_number }}" in _steps_text(_load(DEV)["jobs"]["testpypi"])


# --- prod → PyPI ------------------------------------------------------------

def test_prod_triggers_on_main_push_tag_and_dispatch():
    on = _on(_load(PROD))
    assert "workflow_dispatch" in on
    assert on["push"]["branches"] == ["main"]
    assert any(t.startswith("bloomcli-v") for t in on["push"]["tags"])


def test_prod_publishes_to_real_pypi_with_oidc():
    wf = _load(PROD)
    assert wf["permissions"]["id-token"] == "write"
    job = wf["jobs"]["pypi"]
    assert job["environment"] == "pypi"
    text = _steps_text(job)
    assert "uv publish --trusted-publishing always" in text
    assert "test.pypi.org" not in text


def test_prod_has_immutability_guard():
    job = _load(PROD)["jobs"]["pypi"]
    text = _steps_text(job)
    assert "pypi.org/pypi/bloomcli/" in text  # queries existing version
    # build + publish are gated on the guard's "does not exist" result.
    gated = [s for s in job["steps"] if "exists == 'false'" in str(s.get("if", ""))]
    assert len(gated) >= 2


# --- version-bump pre-merge check -------------------------------------------

def test_bump_check_runs_on_staging_and_main_prs():
    on = _on(_load(BUMP))
    assert set(on["pull_request"]["branches"]) == {"staging", "main"}
    # No path filter, so it always reports a status (required-check friendly).
    assert "paths" not in on["pull_request"]


def test_bump_check_inspects_bloomcli_src_and_can_fail():
    text = _steps_text(_load(BUMP)["jobs"]["version-bump"])
    assert "bloomcli/src" in text
    assert "bloomcli/pyproject.toml" in text
    assert "exit 1" in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
