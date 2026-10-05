"""Regression guard for the npm release + version workflows (GitHub Packages).

release-npm.yml fires only on a published Release or a manual dispatch, and
version-npm.yml is dispatch-only, so PR CI never exercises either. This test is
the only pre-merge gate on their shape. Where a step's behaviour matters, it runs
the workflow's real `run:` script rather than re-implementing it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release-npm.yml"
VERSION = WORKFLOWS / "version-npm.yml"
PACKAGES = ("bloom-js", "bloom-fs")
SCOPE = "@salk-harnessing-plants-initiative"

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


def _jobs() -> dict:
    return _load(RELEASE)["jobs"]


def _step(job: str, name: str) -> dict:
    return next(s for s in _jobs()[job]["steps"] if s.get("name") == name)


def _steps_text(job: dict) -> str:
    return "\n".join(
        f"{s.get('run', '')}\n{s.get('uses', '')}\n{s.get('env', '')}\n{s.get('with', '')}"
        for s in job["steps"]
    )


def _run(script: str, tmp_path: Path, cwd: Path | None = None, **env: str):
    """Run a step's script with GITHUB_OUTPUT captured; returns (result, outputs)."""
    out = tmp_path / "github_output"
    out.write_text("")
    result = subprocess.run(
        [BASH, "-c", script],
        env={**os.environ, "GITHUB_OUTPUT": str(out), **env},
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=20,
    )
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return result, outputs


# --- triggers and the package guard -----------------------------------------

def test_release_triggers_only_on_release_and_dispatch():
    on = _on(_load(RELEASE))
    assert set(on) == {"release", "workflow_dispatch"}
    assert on["release"]["types"] == ["published"]
    assert on["workflow_dispatch"]["inputs"]["package"]["options"] == list(PACKAGES)


def _guard_permits(event_name: str, tag: str | None = None) -> bool:
    """Evaluates the exact `A || B || C` shape of validate-release's `if:`."""
    condition = _jobs()["validate-release"]["if"]
    clauses = [c.strip() for c in condition.split("||")]
    assert clauses == [
        "github.event_name != 'release'",
        "startsWith(github.event.release.tag_name, 'bloom-js-')",
        "startsWith(github.event.release.tag_name, 'bloom-fs-')",
    ], condition
    if event_name != "release":
        return True
    return bool(tag) and tag.lower().startswith(("bloom-js-", "bloom-fs-"))


def test_guard_runs_for_npm_tags_and_skips_other_packages():
    assert _guard_permits("workflow_dispatch") is True
    assert _guard_permits("release", "bloom-js-v0.3.0") is True
    assert _guard_permits("release", "bloom-fs-v0.3.1-dev.0") is True
    assert _guard_permits("release", "bloomctl-v0.1.0a8") is False
    assert _guard_permits("release", "bloommcp-v0.1.0a1") is False


# --- resolving the package --------------------------------------------------

@pytest.mark.parametrize(
    ("tag", "package"), [("bloom-js-v0.3.0", "bloom-js"), ("bloom-fs-v0.3.1-dev.2", "bloom-fs")]
)
def test_resolve_maps_release_tag_to_package(tmp_path, tag, package):
    script = _step("validate-release", "Resolve package")["run"]
    result, outputs = _run(script, tmp_path, EVENT="release", TAG=tag, INPUT_PACKAGE="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert outputs == {"package": package, "dir": f"packages/{package}"}


def test_resolve_rejects_an_unknown_tag(tmp_path):
    script = _step("validate-release", "Resolve package")["run"]
    result, outputs = _run(script, tmp_path, EVENT="release", TAG="BLOOM-JS-v0.3.0", INPUT_PACKAGE="")
    assert result.returncode == 1
    assert "::error::" in result.stdout
    assert outputs == {}


def test_resolve_uses_the_dispatch_input_and_rejects_anything_else(tmp_path):
    script = _step("validate-release", "Resolve package")["run"]
    ok, outputs = _run(script, tmp_path, EVENT="workflow_dispatch", TAG="", INPUT_PACKAGE="bloom-fs")
    assert ok.returncode == 0 and outputs["dir"] == "packages/bloom-fs"
    bad, _ = _run(script, tmp_path, EVENT="workflow_dispatch", TAG="", INPUT_PACKAGE="../web")
    assert bad.returncode == 1


# --- version, dist-tag, tag match, changelog --------------------------------

def _read_version(tmp_path: Path, version: str):
    """Run the version step with a stub `node` that prints `version`, so the test
    checks the channel rule itself and doesn't need Node installed."""
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "node").write_text(f"#!/bin/sh\necho '{version}'\n")
    (stub / "node").chmod(0o755)
    script = _step("validate-release", "Read version from package.json")["run"]
    return _run(script, tmp_path, cwd=tmp_path, PATH=f"{stub}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.parametrize(
    ("version", "dist_tag"), [("0.3.0", "latest"), ("1.10.2", "latest"), ("0.3.1-dev.0", "dev")]
)
def test_version_selects_the_channel(tmp_path, version, dist_tag):
    result, outputs = _read_version(tmp_path, version)
    assert result.returncode == 0, result.stdout
    assert outputs == {"version": version, "dist_tag": dist_tag}


@pytest.mark.parametrize("version", ["0.3.0-rc.0", "0.3.0-dev", "0.3.0-dev.1.2", "0.3"])
def test_other_prerelease_shapes_are_refused(tmp_path, version):
    result, outputs = _read_version(tmp_path, version)
    assert result.returncode == 1
    assert "::error::" in result.stdout
    assert outputs == {}


@pytest.mark.parametrize(
    ("tag", "package", "version", "ok"),
    [
        ("bloom-js-v0.3.0", "bloom-js", "0.3.0", True),
        ("bloom-fs-v0.3.1-dev.0", "bloom-fs", "0.3.1-dev.0", True),
        ("bloom-js-v0.3.1", "bloom-js", "0.3.0", False),
        ("bloom-js-v0.3.0", "bloom-fs", "0.3.0", False),
    ],
)
def test_tag_must_match_the_package_version(tmp_path, tag, package, version, ok):
    script = _step("validate-release", "Validate tag matches version")["run"]
    result, _ = _run(script, tmp_path, TAG=tag, PACKAGE=package, VERSION=version)
    assert (result.returncode == 0) is ok, result.stdout
    if not ok:
        assert "::error::" in result.stdout


def test_changelog_entry_is_required(tmp_path):
    script = _step("validate-release", "Validate changelog entry exists")["run"]
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [0.3.0]\n")
    found, _ = _run(script, tmp_path, cwd=tmp_path, VERSION="0.3.0")
    assert found.returncode == 0
    # 0.3.0 must not satisfy 0.3.0-dev.0, and dots are literal, not regex wildcards.
    for missing in ("0.3.0-dev.0", "0x3x0"):
        result, _ = _run(script, tmp_path, cwd=tmp_path, VERSION=missing)
        assert result.returncode == 1, missing


@pytest.mark.parametrize("package", PACKAGES)
def test_each_package_has_a_changelog_entry_for_its_version(package):
    pkg_dir = REPO_ROOT / "packages" / package
    version = json.loads((pkg_dir / "package.json").read_text())["version"]
    assert f"## [{version}]" in (pkg_dir / "CHANGELOG.md").read_text()


# --- channel: stable from main, dev from staging -----------------------------

@pytest.fixture
def branches(tmp_path):
    """A repo where origin/main has one commit and origin/staging one more."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "on main")
    main = git("rev-parse", "HEAD")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "staging only")
    staging = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", main)
    git("update-ref", "refs/remotes/origin/staging", staging)
    return repo, main, staging


@pytest.mark.parametrize(
    ("dist_tag", "prerelease", "commit", "ok"),
    [
        ("latest", "false", "main", True),
        ("latest", "false", "staging", False),  # not yet promoted
        ("latest", "true", "main", False),  # stable must not be marked pre-release
        ("dev", "true", "staging", True),
        ("dev", "true", "main", True),  # main's commits are already on staging
        ("dev", "false", "staging", False),  # dev must be marked pre-release
    ],
)
def test_release_channel_is_enforced(tmp_path, branches, dist_tag, prerelease, commit, ok):
    repo, main, staging = branches
    sha = {"main": main, "staging": staging}[commit]
    script = _step("validate-release", "Validate release channel")["run"]
    result, _ = _run(script, tmp_path, cwd=repo, DIST_TAG=dist_tag, PRERELEASE=prerelease, SHA=sha)
    assert (result.returncode == 0) is ok, result.stdout + result.stderr
    if not ok:
        assert "::error::" in result.stdout


def test_dev_commit_off_staging_is_refused(tmp_path, branches):
    repo, _, _ = branches
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "feature"], cwd=repo, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                         text=True, check=True).stdout.strip()
    script = _step("validate-release", "Validate release channel")["run"]
    result, _ = _run(script, tmp_path, cwd=repo, DIST_TAG="dev", PRERELEASE="true", SHA=sha)
    assert result.returncode == 1


def test_validate_checks_out_full_history_for_the_channel_check():
    checkout = _jobs()["validate-release"]["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["fetch-depth"] == 0


# --- jobs, credentials, artifact handoff ------------------------------------

def test_three_jobs_chained_in_order():
    jobs = _jobs()
    assert set(jobs) == {"validate-release", "build-and-verify", "build-and-publish"}
    assert jobs["build-and-verify"]["needs"] == "validate-release"
    assert jobs["build-and-publish"]["needs"] == "build-and-verify"


def test_only_the_publish_job_can_write_packages():
    jobs = _jobs()
    assert _load(RELEASE)["permissions"] == {"contents": "read"}
    assert "permissions" not in jobs["validate-release"]
    assert jobs["build-and-verify"]["permissions"] == {"contents": "read", "packages": "read"}
    assert "environment" not in jobs["build-and-verify"]
    publish = jobs["build-and-publish"]
    assert publish["permissions"] == {"contents": "read", "packages": "write"}
    assert publish["environment"] == "github-packages"
    assert "id-token" not in RELEASE.read_text(encoding="utf-8")


def test_publish_job_runs_no_install_or_build():
    text = _steps_text(_jobs()["build-and-publish"])
    assert "actions/checkout" not in text
    for forbidden in ("npm ci", "npm install", "npx ", "tsc"):
        assert forbidden not in text, forbidden


def test_publish_goes_to_github_packages_only_on_a_release():
    wf = _load(RELEASE)
    assert wf["env"] == {"SCOPE": SCOPE, "REGISTRY": "https://npm.pkg.github.com"}
    raw = RELEASE.read_text(encoding="utf-8")
    assert "NPM_TOKEN" not in raw and "registry.npmjs.org" not in raw
    steps = _jobs()["build-and-publish"]["steps"]
    publish = [s for s in steps if "npm publish" in str(s.get("run", ""))]
    assert len(publish) == 1
    assert publish[0]["if"] == "github.event_name == 'release'"
    assert publish[0]["run"] == 'npm publish release/*.tgz --tag "$DIST_TAG"'
    assert publish[0]["env"]["NODE_AUTH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"


def test_release_builds_do_not_use_a_dependency_cache():
    for job in _jobs().values():
        for step in job["steps"]:
            if "setup-node" in step.get("uses", ""):
                assert "cache" not in step.get("with", {}), job.get("name")


def test_tarball_is_installed_outside_the_workspace_before_upload():
    step = _step("build-and-verify", "Verify the tarball installs and loads")
    assert "mktemp -d" in step["run"]
    assert 'npm install --no-audit --no-fund "$TARBALL"' in step["run"]
    assert "require(name)" in step["run"]
    # The token is referenced from .npmrc, never written into it.
    assert "_authToken=${NODE_AUTH_TOKEN}" in step["run"]


def test_checksum_recorded_and_reverified_across_the_job_boundary():
    verify = _steps_text(_jobs()["build-and-verify"])
    publish = _steps_text(_jobs()["build-and-publish"])
    assert "sha256sum release/*.tgz > release.sha256" in verify
    assert "sha256sum -c release.sha256" in verify
    assert "sha256sum -c release.sha256" in publish
    upload = _step("build-and-verify", "Upload the verified artifact")
    download = _step("build-and-publish", "Download the verified artifact")
    assert upload["with"]["name"] == download["with"]["name"]


# --- version workflow -------------------------------------------------------

def test_version_workflow_is_dispatch_only():
    inputs = _on(_load(VERSION))["workflow_dispatch"]["inputs"]
    assert set(_on(_load(VERSION))) == {"workflow_dispatch"}
    assert inputs["package"]["options"] == list(PACKAGES)
    assert {"patch", "minor", "major", "prerelease"} <= set(inputs["bump_type"]["options"])


def test_version_workflow_bumps_without_installing_and_opens_a_pr():
    wf = _load(VERSION)
    assert wf["concurrency"]["group"]
    text = _steps_text(wf["jobs"]["bump-version"])
    # npm version would otherwise run a full install, executing package scripts
    # in a job that holds contents: write.
    assert text.count("--no-workspaces-update") == 3
    assert "--preid dev" in text
    assert "npm install --package-lock-only --ignore-scripts" in text
    assert "peter-evans/create-pull-request" in text


def test_bloom_js_bump_moves_bloom_fs_onto_the_new_range():
    step = next(s for s in _load(VERSION)["jobs"]["bump-version"]["steps"]
                if s.get("name") == "Point bloom-fs at the new bloom-js")
    assert step["if"] == "inputs.package == 'bloom-js'"
    assert f"dependencies.{SCOPE}/bloom-js=^$NEW_VERSION" in step["run"]


def test_custom_version_reaches_the_script_only_through_env():
    raw = VERSION.read_text(encoding="utf-8")
    run_blocks = "\n".join(str(s.get("run", "")) for s in _load(VERSION)["jobs"]["bump-version"]["steps"])
    assert "inputs.custom_version" not in run_blocks
    assert "CUSTOM_VERSION: ${{ inputs.custom_version }}" in raw


# --- packages and pinning ---------------------------------------------------

@pytest.mark.parametrize("package", PACKAGES)
def test_package_metadata_targets_github_packages(package):
    pkg = json.loads((REPO_ROOT / "packages" / package / "package.json").read_text())
    assert pkg["name"] == f"{SCOPE}/{package}"
    # GitHub links the package to this repo (and its access) through this field.
    assert pkg["repository"]["url"] == "git+https://github.com/Salk-Harnessing-Plants-Initiative/bloom.git"
    assert pkg["repository"]["directory"] == f"packages/{package}"
    assert pkg["publishConfig"] == {"registry": "https://npm.pkg.github.com"}
    assert pkg["files"] == ["dist"]
    assert "typescript" not in pkg.get("dependencies", {})


def test_bloom_fs_depends_on_the_current_bloom_js():
    js = json.loads((REPO_ROOT / "packages/bloom-js/package.json").read_text())
    fs = json.loads((REPO_ROOT / "packages/bloom-fs/package.json").read_text())
    assert fs["dependencies"][f"{SCOPE}/bloom-js"] == f"^{js['version']}"


def test_every_action_is_sha_pinned():
    sha = re.compile(r"^[^@]+@[0-9a-f]{40}$")
    for path in (RELEASE, VERSION):
        for job in _load(path)["jobs"].values():
            for step in job["steps"]:
                if step.get("uses"):
                    assert sha.match(step["uses"]), f"{path.name}: {step['uses']} is not SHA-pinned"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
