"""Regression-guard: the bloommcp live_smoke/live_smoke_slow CI split stays intact.

Enforces the "CI vs Pre-Merge Smoke Split" requirement added by
``openspec/changes/archive/2026-08-09-add-bloommcp-cylinder-smoke-coverage/specs/bloommcp-smoke-testing/spec.md``.

Two independent guards, both static (no live dev stack, no bloommcp deps needed):

1. The `dev-stack-smoke` job's granular-smoke pytest step excludes `live_smoke_slow` --
   parallel to ``test_bloommcp_live_smoke_gate.py``'s existing `make bloommcp-smoke`
   step-presence guard, matched on the step's `run:` body rather than a fixed index.
2. Every test file under `bloommcp/tests/smoke/` that mentions `live_smoke_slow`
   also declares `live_smoke` -- otherwise such a test would evade `python-audit`'s
   `not live_smoke` exclusion (it lacks that marker) and run, unmarked and
   infra-free, in a job with no dev stack up.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests.unit._workflow_helpers import _logical_lines

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
SMOKE_DIR = REPO_ROOT / "bloommcp" / "tests" / "smoke"

JOB = "dev-stack-smoke"

# Matches "pytest.mark.live_smoke" but NOT "pytest.mark.live_smoke_slow" (the naive
# substring check `"live_smoke" in text` is always true whenever `"live_smoke_slow"`
# is present, since the former is a literal substring of the latter).
_LIVE_SMOKE_MARKER = re.compile(r"pytest\.mark\.live_smoke(?!_slow)")
_LIVE_SMOKE_SLOW_MARKER = re.compile(r"pytest\.mark\.live_smoke_slow")


def _job_steps() -> list[dict]:
    workflow = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs") or {}
    assert JOB in jobs, f"pr-checks.yml has no {JOB!r} job"
    return jobs[JOB].get("steps") or []


def _logical_run(step: dict) -> str:
    return " ".join(line for _, line in _logical_lines(str(step.get("run") or "")))


def _smoke_pytest_step() -> dict | None:
    for step in _job_steps():
        run = _logical_run(step)
        if "pytest tests/smoke/" in run:
            return step
    return None


def test_dev_stack_smoke_runs_the_granular_smoke_subset() -> None:
    """The `dev-stack-smoke` job runs `pytest tests/smoke/`."""
    step = _smoke_pytest_step()
    assert step is not None, (
        f"pr-checks.yml: {JOB}: no step runs `pytest tests/smoke/`. The granular "
        "smoke coverage gate is missing — see "
        "openspec/changes/add-bloommcp-cylinder-smoke-coverage."
    )


def test_dev_stack_smoke_excludes_live_smoke_slow() -> None:
    """The granular-smoke step's `-m` filter excludes `live_smoke_slow`.

    Without this, the numerically-risky subset (mahalanobis/gmm on cylinder, the
    per-trait MixedLM heritability/variance-decomposition plots) would run on every
    PR instead of only via `/pre-merge`.
    """
    step = _smoke_pytest_step()
    assert step is not None, "smoke step missing (see test above)"
    run = _logical_run(step)
    assert "not live_smoke_slow" in run, (
        f"pr-checks.yml: {JOB}: the `pytest tests/smoke/` step's `-m` filter "
        f"({run!r}) must contain `not live_smoke_slow`, or the numerically-risky "
        "smoke subset would run on every PR."
    )


def test_every_live_smoke_slow_test_file_also_declares_live_smoke() -> None:
    """Every ``bloommcp/tests/smoke/*.py`` file mentioning `live_smoke_slow` also
    declares plain `live_smoke` somewhere in the same file.

    This is the invariant `python-audit`'s single `-m "not integration and not
    live_smoke"` filter relies on (see design.md Decision 1): `live_smoke_slow`
    tests all carry `live_smoke` too, so no third filter term is needed. A test
    file that marks `live_smoke_slow` without also marking `live_smoke` would
    silently evade that filter and run, infra-free, in `python-audit`.
    """
    assert SMOKE_DIR.is_dir(), f"{SMOKE_DIR} does not exist"
    offenders = []
    for path in sorted(SMOKE_DIR.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        if _LIVE_SMOKE_SLOW_MARKER.search(text) and not _LIVE_SMOKE_MARKER.search(text):
            offenders.append(path.relative_to(REPO_ROOT))
    assert not offenders, (
        f"These files mark `live_smoke_slow` without also marking plain `live_smoke`, "
        f"so python-audit's `not live_smoke` filter would not exclude them: {offenders}"
    )
