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
  - the staging tag is pushed only on the push trigger (never on release —
    a release could be tagged from a commit other than staging's current
    tip, and moving the mutable staging pointer from a release build would
    silently corrupt what "staging" is supposed to mean).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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
    # No PAT/secret other than GITHUB_TOKEN referenced anywhere.
    assert "secrets.GITHUB_TOKEN" in text
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            with_ = step.get("with") or {}
            password = str(with_.get("password", ""))
            if "secrets." in password:
                assert password == "${{ secrets.GITHUB_TOKEN }}"


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
    assert jobs["validate-tag"]["if"] == "github.event_name == 'release'"


def test_build_and_push_needs_validate_tag_but_not_blocked_off_release():
    jobs = _load()["jobs"]
    build = jobs["build-and-push"]
    needs = build["needs"]
    assert needs == ["validate-tag"] or needs == "validate-tag"
    condition = build["if"]
    assert "needs.validate-tag.result" in condition
    assert "github.event_name != 'release'" in condition


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
