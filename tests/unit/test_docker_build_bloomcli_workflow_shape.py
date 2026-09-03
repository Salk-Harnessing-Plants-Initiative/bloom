"""Regression guard for the bloomctl GHCR-publishing workflow.

docker-build-bloomcli.yml's push-triggered paths (push to staging, a
published Release, workflow_dispatch) can never be exercised by PR CI before
they land — mirroring tests/unit/test_release_bloomcli_workflow_shape.py's
own justification for release-bloomcli.yml/version-bloomcli.yml — so this
unit test is the pre-merge gate on its shape. It locks in the properties
design.md signed off on:
  - triggers ONLY on push-to-staging (path-filtered to bloomcli/**),
    release (published), or workflow_dispatch — never pull_request, never
    push to any other branch (PR-time validation lives in pr-checks.yml
    instead, see test_pr_checks_docker_build_bloomcli.py);
  - GHCR auth via secrets.GITHUB_TOKEN + packages: write only, no other PAT;
  - tag derivation never uses docker/metadata-action's type=semver (bloomctl
    versions are PEP 440, not semver) — the release-version tag is instead
    derived via the same TAG#bloomctl-v / TAG_VERSION#v} prefix-stripping
    shell logic release-bloomcli.yml already uses;
  - a validate-tag job gates the release-triggered push on that derived
    version actually matching bloomcli/pyproject.toml's version;
  - validate-tag also skips cleanly (not failed) for a Release tagged for a
    different monorepo package (e.g. bloommcp-vX.Y.Z) — added in the #663
    review after this workflow produced a permanent, misleading red X on
    every bloommcp release (it was untouched by that PR, so it never got
    the tag-prefix guard release-bloomcli.yml/release-bloommcp.yml did);
  - the staging tag is pushed only on the push trigger (never on release —
    a release could be tagged from a commit other than staging's current
    tip, and moving the mutable staging pointer from a release build would
    silently corrupt what "staging" is supposed to mean).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

# On Windows, plain "bash" can resolve to WSL's bash.exe (which expects
# /mnt/c/-style paths, not native Windows paths) ahead of Git Bash on PATH —
# prefer Git Bash explicitly when present; CI (ubuntu-latest) has neither
# ambiguity and just uses whatever "bash" resolves to there.
_GIT_BASH = r"C:\Program Files\Git\usr\bin\bash.exe"
BASH = _GIT_BASH if Path(_GIT_BASH).exists() else shutil.which("bash") or "bash"

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "docker-build-bloomcli.yml"

EXPECTED_IMAGE = "ghcr.io/salk-harnessing-plants-initiative/bloomctl"


def _load() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    # PyYAML parses the bare key `on` as boolean True (YAML 1.1).
    return wf.get("on") or wf.get(True)


def _raw() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _steps_text(job: dict) -> str:
    parts = []
    for s in job["steps"]:
        parts.append(str(s.get("run", "")))
        parts.append(str(s.get("uses", "")))
        parts.append(str(s.get("with", "")))
    return "\n".join(parts)


# --- triggers ---------------------------------------------------------------


def test_triggers_are_exactly_push_release_dispatch():
    on = _on(_load())
    assert set(on) == {"push", "release", "workflow_dispatch"}


def test_push_trigger_is_staging_only_with_exact_path_filter():
    on = _on(_load())
    push = on["push"]
    assert push["branches"] == ["staging"]
    assert push["paths"] == ["bloomcli/**"]


def test_release_trigger_is_published_only():
    on = _on(_load())
    assert on["release"]["types"] == ["published"]


# --- auth --------------------------------------------------------------------


def test_packages_write_permission_present():
    wf = _load()
    text = _raw()
    assert "packages: write" in text
    # No PAT/secret other than GITHUB_TOKEN referenced anywhere — check every
    # `with:` value (not just `password`, e.g. `token` too), every `env:`
    # value, and every `run:` script for a stray secrets.* reference.
    assert "secrets.GITHUB_TOKEN" in text
    secret_pattern = re.compile(r"secrets\.(\w+)")
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            for value in list((step.get("with") or {}).values()) + list(
                (step.get("env") or {}).values()
            ) + [step.get("run", "")]:
                for name in secret_pattern.findall(str(value)):
                    assert name == "GITHUB_TOKEN", (
                        f"unexpected secret reference secrets.{name} in step "
                        f"{step.get('name')!r} — only GITHUB_TOKEN is expected"
                    )


# --- tag derivation: no type=semver, explicit prefix-stripping ---------------


def test_no_type_semver_anywhere():
    assert "type=semver" not in _raw()


def test_embeds_the_exact_prefix_stripping_shell_logic():
    text = _raw()
    assert "TAG#bloomctl-v" in text
    assert "TAG_VERSION#v}" in text


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("bloomctl-v0.1.0a2", "0.1.0a2"),
        ("v0.1.0a2", "0.1.0a2"),
        ("0.1.0a2", "0.1.0a2"),
    ],
)
def test_prefix_stripping_logic_resolves_expected_version(tag: str, expected: str):
    """Defense-in-depth: re-implement the exact shell logic in Python."""
    tag_version = tag
    if tag_version.startswith("bloomctl-v"):
        tag_version = tag_version[len("bloomctl-v"):]
    if tag_version.startswith("v"):
        tag_version = tag_version[len("v"):]
    assert tag_version == expected


# --- validate-tag job ---------------------------------------------------------


def test_validate_tag_job_exists_gated_on_release():
    jobs = _load()["jobs"]
    assert "validate-tag" in jobs
    assert jobs["validate-tag"]["if"] == (
        "github.event_name == 'release' && "
        "startsWith(github.event.release.tag_name, 'bloomctl-')"
    )


def _validate_tag_job_runs(*, event_name: str, tag: str | None) -> bool:
    """Evaluate validate-tag's job-level `if:` behaviorally rather than just
    checking its string contents. This is an `A && B` guard, not the `A || B`
    shape release-bloomcli.yml/release-bloommcp.yml use — validate-tag is
    release-only auxiliary validation (push/workflow_dispatch never need it),
    not the main gate every trigger type must pass through, so `&&` (not
    `||`) is the correct join here. A parametrized truth table (rather than
    only a substring check) would catch a future edit that silently flips
    the join and reintroduces the false-alarm-on-bloommcp-release bug (#663
    review) or, in the other direction, starts silently skipping real
    bloomctl releases too.
    """
    condition = _load()["jobs"]["validate-tag"]["if"]
    clauses = [c.strip() for c in condition.split("&&")]
    assert len(clauses) == 2, f"expected exactly one top-level `&&`, got: {condition!r}"
    is_release, tag_prefix = clauses
    assert is_release == "github.event_name == 'release'", is_release
    assert tag_prefix == "startsWith(github.event.release.tag_name, 'bloomctl-')", tag_prefix

    if event_name != "release":
        return False
    return bool(tag and tag.startswith("bloomctl-"))


def test_validate_tag_guard_truth_table():
    assert _validate_tag_job_runs(event_name="push", tag=None) is False
    assert _validate_tag_job_runs(event_name="workflow_dispatch", tag=None) is False
    assert _validate_tag_job_runs(event_name="release", tag="bloomctl-v0.1.0a2") is True
    assert _validate_tag_job_runs(event_name="release", tag="v0.1.0a2") is False
    # The exact gap this guard closes: a bloommcp release must not produce a
    # failing (or even running) validate-tag job on bloomcli's workflow.
    assert _validate_tag_job_runs(event_name="release", tag="bloommcp-v0.1.0a2") is False


def test_build_and_push_needs_validate_tag_but_not_blocked_off_release():
    jobs = _load()["jobs"]
    build = jobs["build-and-push"]
    needs = build["needs"]
    assert needs == ["validate-tag"] or needs == "validate-tag"
    condition = build["if"]
    assert "needs.validate-tag.result" in condition
    assert "github.event_name != 'release'" in condition


def _run_validate_tag_script(tag: str, pyproject_version: str) -> tuple[int, str, str]:
    """Execute the REAL `run:` script extracted from validate-tag's 'check'
    step (not a Python reimplementation), with a stubbed `uv` on PATH so
    `uv version` prints a controlled version — this exercises the actual
    exit-1-on-mismatch branch, not just the string-stripping formula.
    """
    job = _load()["jobs"]["validate-tag"]
    step = next(s for s in job["steps"] if s.get("id") == "check")
    script = step["run"]

    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "uv"
        # Explicit newline="\n": Path.write_text's platform-default newline
        # translation would emit CRLF on Windows, and bash's shebang-line
        # parser treats a trailing \r as part of the interpreter path
        # ("/bin/sh^M: bad interpreter").
        stub.write_text(
            f"#!/bin/sh\necho 'bloomctl {pyproject_version}'\n", newline="\n"
        )
        stub.chmod(0o755)
        github_output = Path(tmp) / "github_output"
        github_output.write_text("")
        env = {
            **os.environ,
            "TAG": tag,
            "GITHUB_OUTPUT": str(github_output),
            "PATH": f"{tmp}:{os.environ.get('PATH', '')}",
        }
        result = subprocess.run(
            [BASH, "-c", script], env=env, capture_output=True, text=True
        )
        return result.returncode, result.stdout + result.stderr, github_output.read_text()


def test_validate_tag_script_passes_and_records_output_on_matching_tag():
    code, _output, gh_output = _run_validate_tag_script("bloomctl-v0.1.0a2", "0.1.0a2")
    assert code == 0
    assert "version=0.1.0a2" in gh_output


def test_validate_tag_script_fails_on_mismatched_tag():
    code, output, gh_output = _run_validate_tag_script("bloomctl-v0.1.0a3", "0.1.0a2")
    assert code == 1
    assert "does not match" in output
    assert "version=" not in gh_output


# --- image name + tag scheme --------------------------------------------------


def test_image_name_resolves_to_expected_namespace():
    assert EXPECTED_IMAGE in _raw()


def test_staging_tag_only_enabled_on_push_trigger():
    text = _raw()
    assert "value=staging" in text
    assert "enable=${{ github.event_name == 'push' }}" in text


def test_sha_short_tag_present_on_every_trigger():
    text = _raw()
    assert "sha-" in text
    assert "rev-parse --short HEAD" in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
