"""Regression guard for the bloomctl release + version workflows.

These workflows can never be exercised by PR CI before they land
(`release-bloomcli.yml` fires only on a published Release or manual dispatch;
`version-bloomcli.yml` is dispatch-only), so this unit test is the only
pre-merge gate on their shape.

It locks the safety-critical properties the design signed off on:
  - the publish workflow triggers ONLY on a Release (`published`) or
    workflow_dispatch — never on a push or tag;
  - `build-and-publish` is gated by `needs: validate-release`;
  - `build-and-publish` requests the OIDC token (`id-token: write`) and pins the
    `pypi` environment so trusted publishing works, and stores no API token;
  - the actual `uv publish` runs only on a real Release event;
  - the built wheel is smoke-tested (import + `bloomctl --version`) before upload;
  - there is no TestPyPI lane;
  - the version workflow bumps via `uv version` and opens a PR.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release-bloomcli.yml"
VERSION = WORKFLOWS / "version-bloomcli.yml"


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
    # Never publish on a push or a raw tag.
    assert "push" not in on


# --- publish workflow: validate gates publish ------------------------------

def test_publish_needs_validate_release():
    """Publishing stays downstream of validation, now via the build-and-verify job."""
    jobs = _load(RELEASE)["jobs"]
    assert "validate-release" in jobs
    assert "build-and-verify" in jobs
    assert "build-and-publish" in jobs
    assert jobs["build-and-verify"]["needs"] == "validate-release"
    assert jobs["build-and-publish"]["needs"] == "build-and-verify"


def test_validate_checks_tag_changelog_lint_tests():
    text = _steps_text(_load(RELEASE)["jobs"]["validate-release"])
    assert "github.event.release.tag_name" in text  # tag ↔ version match
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
    # No stored PyPI token anywhere in the workflow.
    raw = _raw(RELEASE)
    assert "PYPI_API_TOKEN" not in raw
    assert "test.pypi.org" not in raw  # no TestPyPI lane


def test_publish_step_gated_on_real_release():
    job = _load(RELEASE)["jobs"]["build-and-publish"]
    publish = [s for s in job["steps"] if "uv publish" in str(s.get("run", ""))]
    assert len(publish) == 1
    assert publish[0]["if"] == "github.event_name == 'release'"


def test_built_wheel_is_smoke_tested_before_publish():
    text = _steps_text(_load(RELEASE)["jobs"]["build-and-verify"])
    assert "uv build" in text
    assert "import bloomctl" in text            # wheel imports
    assert "bloomctl --version" in text         # CLI entry point runs
    assert "dist/*.whl" in text                 # from the freshly built wheel


def test_nothing_but_the_upload_runs_in_the_job_holding_the_credential():
    """The smoke runs import every dependency, the second at its newest pre-release.

    Run beside `id-token: write`, a malicious pre-release of any transitive dependency
    could mint a PyPI token or rewrite the wheel about to be uploaded. `--isolated`
    isolates the virtualenv, not the process — so the split is the control.
    """
    jobs = _load(RELEASE)["jobs"]
    publish, verify = jobs["build-and-publish"], jobs["build-and-verify"]

    assert "id-token" not in (verify.get("permissions") or {})
    assert verify.get("environment") is None

    # Checking the artifact and uploading it, and nothing else. `sha256sum` is coreutils;
    # what must never appear here is anything that executes package code.
    allowed = {"sha256sum -c dist.sha256", "uv publish --trusted-publishing always"}
    runs = [str(s.get("run", "")) for s in publish["steps"] if s.get("run")]
    assert set(runs) <= allowed, f"unexpected step in the credentialed job: {runs}"

    # Allowlisted by `uses:` as well. Checking only `run:` steps would let an action be
    # added beside `id-token: write` — arbitrary code next to the credential, which is the
    # one thing this job's existence is meant to prevent.
    allowed_actions = {"astral-sh/setup-uv", "actions/download-artifact"}
    actions = [str(s["uses"]).split("@")[0] for s in publish["steps"] if s.get("uses")]
    assert set(actions) <= allowed_actions, f"unexpected action in the credentialed job: {actions}"
    for forbidden in ("uv build", "twine", "--prerelease=allow", "import_smoke", "--with"):
        assert forbidden not in _steps_text(publish)


# --- version workflow -------------------------------------------------------

def test_version_workflow_is_dispatch_only_with_bump_input():
    on = _on(_load(VERSION))
    assert set(on) == {"workflow_dispatch"}
    inputs = on["workflow_dispatch"]["inputs"]
    assert "bump_type" in inputs
    assert {"patch", "minor", "major"}.issubset(set(inputs["bump_type"]["options"]))


def test_version_workflow_bumps_and_opens_pr():
    wf = _load(VERSION)
    assert wf["permissions"]["contents"] == "write"
    assert wf["permissions"]["pull-requests"] == "write"
    text = _steps_text(wf["jobs"]["bump-version"])
    assert "uv version" in text
    assert "peter-evans/create-pull-request" in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_the_wheel_gate_imports_the_dependency_chain_not_just_bloomctl():
    """#629's gate passed on a build where every real command died.

    `import bloomctl` alone stays green because commands import supabase lazily, so the
    gate has to walk the package and pull the chain in explicitly.
    """
    text = _steps_text(_load(RELEASE)["jobs"]["build-and-verify"])

    assert "walk_packages" in text, "the gate must import every bloomctl module"
    assert "from supabase import create_client" in text
    assert "from postgrest import APIError" in text


def test_the_wheel_gate_also_resolves_with_prereleases():
    """The install users actually did. Without this pass the a4 build looked fine."""
    assert "--prerelease=allow" in _steps_text(_load(RELEASE)["jobs"]["build-and-verify"])


def test_the_published_artifact_is_checksummed_across_the_handoff():
    """The verify job runs third-party code with write access to dist/.

    Removing the credential from the publishing job stops an attacker minting a token; it
    does not stop them replacing the wheel a trusted job then uploads.
    """
    jobs = _load(RELEASE)["jobs"]
    verify, publish = _steps_text(jobs["build-and-verify"]), _steps_text(jobs["build-and-publish"])

    assert "sha256sum dist/*" in verify, "nothing records what was built"
    assert "sha256sum -c" in verify, "the upload is not checked against the build"
    assert "sha256sum -c" in publish, "the publish job trusts the artifact blindly"


def test_the_entry_point_check_would_notice_a_lost_handler():
    """`bloomctl --version` and any ClickException read the same either way.

    Only an unhandled exception distinguishes the wrapper from the bare CLI.
    """
    text = _steps_text(_load(RELEASE)["jobs"]["build-and-verify"])

    assert "bloomctl.errors:main" in text, "nothing pins the shipped console script"
    assert "Details written to" in text, "nothing asserts the handler-only behaviour"
