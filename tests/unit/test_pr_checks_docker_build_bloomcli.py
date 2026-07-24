"""Regression guard: bloomcli's entry in pr-checks.yml's docker-build job.

pr-checks.yml's docker-build job is not a single reusable Trivy step per
image — each of the five existing images gets three separate step blocks
(build, report-only scan, and a *separate* blocking CVE gate) plus two
"for img in ..." loops inside the "Generate Trivy report" step. It's easy to
wire only some of these for a new image and leave its CVE gate silently
non-enforcing. This test locks in that bloomcli gets the full treatment, and
that its build context/dockerfile stay identical to whatever
docker-build-bloomcli.yml uses, so the two build paths can't silently
diverge (mirrors test_pr_checks_workflow_shape.py's
test_overlay_build_context_matches_prod pattern for the compose overlay).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
DOCKER_BUILD_BLOOMCLI = REPO_ROOT / ".github" / "workflows" / "docker-build-bloomcli.yml"


def _docker_build_job() -> dict:
    wf = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    return wf["jobs"]["docker-build"]


def _step_named(job: dict, name: str) -> dict:
    for step in job["steps"]:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in docker-build job")


def test_bloomcli_build_step_exists_and_does_not_push() -> None:
    step = _step_named(_docker_build_job(), "Build bloomcli image")
    with_ = step["with"]
    assert with_["context"] == "./bloomcli"
    assert with_["file"] == "bloomcli/Dockerfile"
    assert with_["tags"] == "bloomcli:ci"
    assert with_["load"] is True
    assert with_.get("push") in (None, False)


def test_bloomcli_report_only_scan_step_exists() -> None:
    step = _step_named(_docker_build_job(), "Scan bloomcli image")
    with_ = step["with"]
    assert with_["image-ref"] == "bloomcli:ci"
    assert with_["severity"] == "CRITICAL,HIGH"
    assert str(with_["exit-code"]) == "0"


def test_bloomcli_has_a_separate_blocking_critical_cve_gate() -> None:
    """The report-only scan does NOT block merge — this step does.

    Omitting this step (while keeping only the report-only scan) would
    silently make bloomctl's CVE scan non-enforcing while every sibling
    image's is.
    """
    job = _docker_build_job()
    report_step = _step_named(job, "Scan bloomcli image")
    gate_step = _step_named(job, "Check bloomcli for critical CVEs")
    assert gate_step is not report_step
    with_ = gate_step["with"]
    assert with_["image-ref"] == "bloomcli:ci"
    assert with_["severity"] == "CRITICAL"
    assert str(with_["exit-code"]) == "1"


def test_bloomcli_appears_in_both_trivy_report_loops() -> None:
    job = _docker_build_job()
    report_step = _step_named(job, "Generate Trivy report")
    run = str(report_step["run"])
    loop_lines = [
        line for line in run.splitlines() if line.strip().startswith("for img in ")
    ]
    assert len(loop_lines) == 2, (
        f"expected exactly 2 'for img in ...' loops in the Trivy report step, "
        f"found {len(loop_lines)}"
    )
    for line in loop_lines:
        assert "bloomcli" in line, f"'bloomcli' missing from loop line: {line!r}"


@pytest.mark.skipif(
    not DOCKER_BUILD_BLOOMCLI.exists(),
    reason="docker-build-bloomcli.yml not created yet",
)
def test_bloomcli_build_context_matches_docker_build_bloomcli_workflow() -> None:
    """Sanity: the two places that build bloomcli's image must agree."""
    pr_checks_step = _step_named(_docker_build_job(), "Build bloomcli image")
    pr_checks_with = pr_checks_step["with"]

    dedicated = yaml.safe_load(DOCKER_BUILD_BLOOMCLI.read_text(encoding="utf-8"))
    found = False
    for job in dedicated["jobs"].values():
        for step in job.get("steps") or []:
            uses = str(step.get("uses", ""))
            if "build-push-action" in uses:
                with_ = step.get("with") or {}
                assert with_.get("context") == pr_checks_with["context"]
                assert with_.get("file") == pr_checks_with["file"]
                found = True
    assert found, "no docker/build-push-action step found in docker-build-bloomcli.yml"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
