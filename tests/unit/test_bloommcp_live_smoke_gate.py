"""Regression-guard: the bloommcp live-persistence smoke CI gate exists.

Enforces the "Persistence Smoke CI Gate" requirement added by
``openspec/changes/add-bloommcp-live-persistence-smoke/specs/bloommcp-result-store/spec.md``.

The real behavioural assertion (does the live write path persist a v3 manifest
whose hashes match the stored bytes?) can only run in CI — it needs the dev stack
(Supabase + storage-api + MinIO) up and migrated. This local guard instead asserts
the gate is PRESENT and correctly ordered in ``.github/workflows/pr-checks.yml`` so
it cannot be silently deleted or hollowed out:

  * the dev-stack job runs ``make bloommcp-smoke``;
  * it runs ``make migrate-local`` BEFORE ``make bloommcp-smoke`` — the storage-schema
    grants the write path needs are applied by ``migrate-local``, so a smoke that ran
    first would fail spuriously;
  * the job keeps an ``if: always()`` ``docker compose ... down -v`` teardown, so a
    leaked stack cannot mask a later run.

Matches on step *presence and relative order* (never a fixed index), so reordering
unrelated steps does not break the guard. Reuses the logical-line joiner from
``tests/unit/_workflow_helpers.py`` so a command split across backslash
continuations is still detected.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.unit._workflow_helpers import _logical_lines

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

JOB = "dev-stack-smoke"


def _job_steps() -> list[dict]:
    """Return the ``dev-stack-smoke`` job's steps from pr-checks.yml."""
    workflow = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs") or {}
    assert JOB in jobs, f"pr-checks.yml has no {JOB!r} job"
    return jobs[JOB].get("steps") or []


def _logical_run(step: dict) -> str:
    """Join a step's ``run`` body into one string with continuations resolved."""
    return " ".join(line for _, line in _logical_lines(str(step.get("run") or "")))


def _first_index_running(token: str) -> int | None:
    """Index of the first step whose run body contains ``token`` (else None)."""
    for i, step in enumerate(_job_steps()):
        if token in _logical_run(step):
            return i
    return None


def test_live_smoke_step_exists() -> None:
    """The dev-stack job runs ``make bloommcp-smoke``."""
    assert _first_index_running("make bloommcp-smoke") is not None, (
        f"pr-checks.yml: {JOB}: no step runs `make bloommcp-smoke`. The live "
        "persistence gate is missing — see "
        "openspec/changes/add-bloommcp-live-persistence-smoke."
    )


def test_live_smoke_runs_after_migrate_local() -> None:
    """``make bloommcp-smoke`` runs after ``make migrate-local`` (grants ordering)."""
    migrate_idx = _first_index_running("make migrate-local")
    smoke_idx = _first_index_running("make bloommcp-smoke")
    assert migrate_idx is not None, (
        f"pr-checks.yml: {JOB}: expected a `make migrate-local` step before the "
        "smoke (it applies the storage-schema grants the write path needs)."
    )
    assert smoke_idx is not None, "smoke step missing (see test_live_smoke_step_exists)"
    assert migrate_idx < smoke_idx, (
        f"pr-checks.yml: {JOB}: `make bloommcp-smoke` (step {smoke_idx}) must run "
        f"AFTER `make migrate-local` (step {migrate_idx}); the bloommcp write path "
        "depends on the storage grants migrate-local applies."
    )


def test_live_smoke_job_tears_down_stack() -> None:
    """The job retains an ``if: always()`` ``down -v`` teardown."""
    for step in _job_steps():
        cond = str(step.get("if") or "")
        if "always()" in cond and "down -v" in _logical_run(step):
            return
    raise AssertionError(
        f"pr-checks.yml: {JOB}: expected an `if: always()` step running "
        "`docker compose ... down -v` so a leaked dev stack cannot mask later runs."
    )
