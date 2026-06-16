"""Regression-guard: the bloommcp built-wheel clean-import CI gate exists.

Enforces the "CI Gates the Built-Wheel Import" requirement added by
``openspec/changes/add-bloommcp-wheel-import-ci/specs/bloommcp-packaging/spec.md``.

The real behavioural assertion (does the wheel actually import?) can only run
in CI — it needs ``uv build`` plus a network resolve of the heavy runtime
closure (sleap-roots-analyze, statsmodels, umap, scipy, fastmcp, ...). This
local guard instead asserts the gate is PRESENT and correctly shaped in
``.github/workflows/pr-checks.yml`` so it cannot be silently deleted or quietly
narrowed (e.g. dropping a module from the import line, or removing the empty
Supabase env that makes the lazy-validation contract load-bearing).

Mirrors ``tests/unit/test_ci_workflow_uv_conventions.py`` and reuses its
logical-line joiner so a gate whose command is split across backslash
continuations is still detected. Matches on step *presence* (never a fixed
index), so reordering steps in ``python-audit`` does not break the guard.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.unit.test_ci_workflow_uv_conventions import _logical_lines

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"

JOB = "python-audit"
# All four must appear on the import line — `bloom_mcp.server` imports the whole
# tool surface + both validate_env functions, so it is the strongest single
# assertion that the shipped wheel's namespace is intact.
REQUIRED_IMPORTS = (
    "bloom_mcp",
    "bloom_mcp.tools",
    "bloom_mcp.storage",
    "bloom_mcp.server",
)
EMPTY_ENV_VARS = ("SUPABASE_URL", "BLOOM_AGENT_KEY")


def _python_audit_steps() -> list[dict]:
    """Return the ``python-audit`` job's steps from pr-checks.yml."""
    workflow = yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs") or {}
    assert JOB in jobs, f"pr-checks.yml has no {JOB!r} job"
    return jobs[JOB].get("steps") or []


def _logical_run(step: dict) -> str:
    """Join a step's ``run`` body into one string with continuations resolved."""
    return " ".join(line for _, line in _logical_lines(str(step.get("run") or "")))


def _find_gate_step() -> dict | None:
    """Locate the wheel build + clean-import step by shape, not by name/index."""
    for step in _python_audit_steps():
        run = _logical_run(step)
        if "cd bloommcp" in run and "uv build" in run and "--no-project" in run:
            return step
    return None


def test_wheel_import_gate_step_exists() -> None:
    """A step builds the bloommcp wheel and imports it from a clean env."""
    assert _find_gate_step() is not None, (
        f"pr-checks.yml: {JOB}: no step builds the bloommcp wheel and imports it "
        "from a clean (--no-project) env. The built-wheel import gate is missing — "
        "see openspec/changes/add-bloommcp-wheel-import-ci."
    )


def test_wheel_import_gate_covers_all_modules() -> None:
    """The import line names every required module (guards against narrowing)."""
    step = _find_gate_step()
    assert (
        step is not None
    ), "gate step missing (see test_wheel_import_gate_step_exists)"
    run = _logical_run(step)
    missing = [mod for mod in REQUIRED_IMPORTS if mod not in run]
    assert not missing, (
        f"pr-checks.yml: {JOB}: wheel-import gate omits {missing} from its import "
        f"line; it must import all of {list(REQUIRED_IMPORTS)} so a narrowed "
        "namespace still fails the gate."
    )


def test_wheel_import_gate_runs_with_empty_supabase_env() -> None:
    """The gate pins SUPABASE_URL / BLOOM_AGENT_KEY empty (lazy-validation proof)."""
    step = _find_gate_step()
    assert (
        step is not None
    ), "gate step missing (see test_wheel_import_gate_step_exists)"
    env = step.get("env") or {}
    for var in EMPTY_ENV_VARS:
        assert var in env and env[var] == "", (
            f'pr-checks.yml: {JOB}: wheel-import gate must set {var}: "" so the '
            f"lazy-validation contract is load-bearing; got {env.get(var)!r}."
        )
