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
  - three jobs, chained by `needs:`: validate-release -> build-and-verify ->
    build-and-publish;
  - only `build-and-publish` requests the OIDC token (`id-token: write`) and
    pins the `pypi` environment — `build-and-verify`, which runs the
    third-party build/twine/import code, holds neither, so the publish
    credential never shares a job with that code (#629);
  - the actual `uv publish` runs only on a real Release event;
  - the built wheel is smoke-tested (import of bloom_mcp plus the concrete
    Supabase adapters, and `bloom-mcp --version`) before upload, and its
    checksum is recorded before and re-verified after the artifact crosses the
    job boundary;
  - there is no TestPyPI lane;
  - the version workflow bumps via `uv version`, syncs `uv.lock`, and opens a
    PR, with a concurrency guard against overlapping dispatches.
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


def _guard_permits(condition: str, prefix: str, *, event_name: str, tag: str | None) -> bool:
    """Evaluate a job-level `if:` guard of the exact `A || B` shape these
    workflows use, rather than checking each clause's substring in isolation.

    A future edit that silently breaks the join (e.g. `||` flipped to `&&`,
    or the clauses reordered/duplicated) would still contain both substrings
    but permanently disable every real release — this fails loudly on that
    instead of passing. Deliberately not a general GitHub Actions expression
    parser: a condition that isn't exactly this two-clause `||` shape raises.
    """
    clauses = [c.strip() for c in condition.split("||")]
    assert len(clauses) == 2, f"expected exactly one top-level `||`, got: {condition!r}"
    not_release, tag_prefix = clauses
    assert not_release == "github.event_name != 'release'", not_release
    assert tag_prefix == f"startsWith(github.event.release.tag_name, '{prefix}')", tag_prefix

    if event_name != "release":
        return True
    return bool(tag and tag.startswith(prefix))


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


def test_validate_release_guard_truth_table():
    """Exercises the joined `||` expression's actual behavior, not just each
    clause's substring — see `_guard_permits`'s docstring for why."""
    condition = _load(RELEASE)["jobs"]["validate-release"].get("if", "")

    def guard(event_name: str, tag: str | None = None) -> bool:
        return _guard_permits(condition, "bloommcp-", event_name=event_name, tag=tag)

    assert guard("workflow_dispatch") is True
    assert guard("release", tag="bloommcp-v1.0.0") is True
    assert guard("release", tag="bloomctl-v1.0.0") is False
    assert guard("release", tag="bloomcp-v1.0.0") is False  # typo'd prefix


# --- publish workflow: three jobs chained validate -> verify -> publish ----

def test_three_jobs_chained_in_order():
    jobs = _load(RELEASE)["jobs"]
    assert set(jobs) == {"validate-release", "build-and-verify", "build-and-publish"}
    assert jobs["build-and-verify"]["needs"] == "validate-release"
    assert jobs["build-and-publish"]["needs"] == "build-and-verify"


def test_validate_checks_tag_changelog_lint_tests():
    text = _steps_text(_load(RELEASE)["jobs"]["validate-release"])
    assert "github.event.release.tag_name" in text  # tag <-> version match
    assert "CHANGELOG.md" in text                    # changelog entry check
    assert "ruff" in text                            # lint
    assert "pytest" in text                          # tests


# --- credential isolation: only build-and-publish holds the OIDC token -----

def test_only_publish_job_holds_the_oidc_credential():
    """The build/verify job runs third-party code (twine, the wheel's own
    dependency chain) — it must never share a job with the OIDC token (#629).
    """
    jobs = _load(RELEASE)["jobs"]
    verify_perms = jobs["build-and-verify"].get("permissions", {})
    assert verify_perms.get("id-token") != "write"
    assert "environment" not in jobs["build-and-verify"]

    publish_job = jobs["build-and-publish"]
    assert publish_job["permissions"]["id-token"] == "write"
    assert publish_job["environment"] == "pypi"


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


def test_built_wheel_is_smoke_tested_before_upload():
    text = _steps_text(_load(RELEASE)["jobs"]["build-and-verify"])
    assert "uv build" in text
    assert "import bloom_mcp" in text
    assert "bloom-mcp --version" in text
    assert "dist/*.whl" in text


def test_the_wheel_gate_imports_the_concrete_supabase_adapters():
    """build_app() alone doesn't reach these — they're wired by main()'s
    composition root, after the --version early return. Explicit imports here
    close the class of gap bloomcli's #629 exploited.
    """
    text = _steps_text(_load(RELEASE)["jobs"]["build-and-verify"])
    assert "SupabaseReader" in text
    assert "SupabaseResultStore" in text
    assert "from postgrest import APIError" in text
    assert "from supabase import create_client" in text


def test_the_wheel_gate_walks_every_bloom_mcp_module():
    """#629's gate passed on a build where every real command died.

    `import bloom_mcp` alone stays green because sections import supabase
    lazily, so the gate has to walk the package and pull the chain in
    explicitly — matches release-bloomcli.yml's current `main` shape.
    """
    text = _steps_text(_load(RELEASE)["jobs"]["build-and-verify"])
    assert "walk_packages" in text, "the gate must import every bloom_mcp module"


def test_the_wheel_gate_also_resolves_with_prereleases():
    """The install users actually did. Without this pass a broken transitive
    pre-release (e.g. httpx 1.0's removed API) can still look fine (#663
    review — this exact check caught bloommcp's own missing httpx/supabase
    upper bounds before merge)."""
    assert "--prerelease=allow" in _steps_text(_load(RELEASE)["jobs"]["build-and-verify"])


def test_the_entry_point_check_would_notice_a_silent_failure():
    """bloom-mcp has no bloomctl.errors:main-style wrapper to distinguish from
    a bare CLI, but its own documented contract (server.py's main()) can still
    regress silently: `--version` returning before env validation, and a real
    run with no env failing fast rather than hanging in uvicorn.run(). Neither
    is provable by `--version` alone.
    """
    text = _steps_text(_load(RELEASE)["jobs"]["build-and-verify"])
    assert "bloom_mcp.server:main" in text, "nothing pins the shipped console script"
    assert "timeout=10" in text, "nothing guards against a hang on missing env"
    assert "RuntimeError" in text, "nothing asserts a real, no-env run fails loudly"


def test_twine_check_is_pinned():
    """New code, unlike release-bloomcli.yml's inherited unpinned `uvx twine
    check` — nothing to copy forward here."""
    text = _steps_text(_load(RELEASE)["jobs"]["build-and-verify"])
    assert "uvx twine@" in text


# --- artifact handoff: checksum recorded before, re-verified after the ------
# --- job boundary, so a build-time swap can't slip past unnoticed ----------

def test_artifact_checksum_recorded_and_reverified_across_job_boundary():
    verify_text = _steps_text(_load(RELEASE)["jobs"]["build-and-verify"])
    publish_text = _steps_text(_load(RELEASE)["jobs"]["build-and-publish"])
    assert "sha256sum dist/*" in verify_text
    assert "sha256sum -c dist.sha256" in verify_text
    assert "sha256sum -c dist.sha256" in publish_text


def test_verified_artifact_uploaded_and_downloaded_by_name():
    verify_job = _load(RELEASE)["jobs"]["build-and-verify"]
    publish_job = _load(RELEASE)["jobs"]["build-and-publish"]
    upload = [s for s in verify_job["steps"] if s.get("uses", "").startswith("actions/upload-artifact")]
    download = [s for s in publish_job["steps"] if s.get("uses", "").startswith("actions/download-artifact")]
    assert len(upload) == 1
    assert len(download) == 1
    assert upload[0]["with"]["name"] == download[0]["with"]["name"]


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


def test_version_workflow_has_concurrency_guard():
    """create-pull-request force-pushes a fixed-name branch — two overlapping
    dispatches with no guard could clobber each other."""
    wf = _load(VERSION)
    assert "concurrency" in wf
    assert wf["concurrency"]["group"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
