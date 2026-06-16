"""Regression-guard test for CI workflow uv conventions.

Enforces requirements from openspec/changes/harden-ci-uv-conventions/specs/
python-dependency-management/spec.md (composes with pin-python-deps in
openspec/specs/python-dependency-management/spec.md once #160 archives).

Three invariants on every .github/workflows/*.{yml,yaml} file:
  1. No `run:` step installs the `uv` package via pip — parses each logical
     line as a shell command list and verifies `uv` is not a package arg of
     any `pip install` (or `pip3 install`, or `python -m pip install`).
  2. No job that uses uv also has an `actions/setup-python@` step. "Uses uv"
     means: any step `uses: astral-sh/setup-uv@...`, OR any `run:` line whose
     first token in any pipeline segment (after splitting on shell command
     separators) is exactly `uv`.
  3. Any `run:` line containing `uv run` AND `pytest` MUST use `--extra test`
     and MUST NOT contain `--with`.

All checks operate on **logical lines** — physical lines connected by trailing
backslash continuations are joined first. This prevents a regression from
slipping through by splitting a forbidden command across two physical lines.

Scope cut: top-level workflow files only. Composite actions and reusable
workflows referenced via `uses: ./...` or `uses: org/repo/...` are not
traversed.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Iterator, NamedTuple

import pytest
import yaml

from tests.unit._workflow_helpers import _logical_lines, _strip_line_comment

# Re-exported for backwards compatibility — these helpers now live in
# tests/unit/_workflow_helpers.py so sibling workflow-shape guards can reuse
# them without importing this test module.
__all__ = ["_logical_lines", "_strip_line_comment"]

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Splits a logical line into pipeline segments at unquoted `&&`, `||`, `;`, `|`.
# We use a regex (not shlex) for the split because shlex doesn't expose operator
# tokens — the regex is fine here: workflow `run` blocks don't typically embed
# those operators inside quoted strings.
_PIPELINE_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


class Step(NamedTuple):
    workflow: Path
    job: str
    index: int  # zero-based position in the job's `steps:` list
    name: str  # step `name:` or "<unnamed>"
    run: str  # `run:` body or ""
    uses: str  # `uses:` value or ""


def _iter_steps() -> Iterator[Step]:
    """Yield every (workflow, job, step) triple from .github/workflows/*.{yml,yaml}."""
    paths = sorted(
        list(WORKFLOWS_DIR.glob("*.yml")) + list(WORKFLOWS_DIR.glob("*.yaml"))
    )
    if not paths:
        pytest.fail(
            f"No workflow files found under {WORKFLOWS_DIR} — the regression-"
            f"guard test cannot run. Verify the test is invoked from the "
            f"repository root."
        )
    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        if not isinstance(doc, dict):
            continue
        jobs = doc.get("jobs") or {}
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps") or []
            if not isinstance(steps, list):
                continue
            for idx, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                yield Step(
                    workflow=path,
                    job=str(job_name),
                    index=idx,
                    name=str(step.get("name", "<unnamed>")),
                    run=str(step.get("run") or ""),
                    uses=str(step.get("uses") or ""),
                )


def _tokenize(cmd: str) -> list[str]:
    """Best-effort shell tokenization. Falls back to whitespace split on parse error."""
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return cmd.split()


def _is_pip_install_uv_command(cmd: str) -> bool:
    """True if `cmd` is a `pip install ... uv ...` invocation that installs the uv package.

    Handles: `pip install uv`, `pip3 install uv==0.4`, `python -m pip install --upgrade uv`,
    `python3 -m pip install -U uv`. Rejects: `pip install uv-helper`, `pip install pyuv`,
    and any non-pip-install command (callers split a logical line into pipeline segments
    first, so `pip install pyyaml && uv run ...` is fed to this function as two separate
    commands and only `pip install pyyaml` is evaluated against this rule).
    """
    tokens = _tokenize(cmd)
    if not tokens:
        return False
    i = 0
    if (
        tokens[i] in ("python", "python3")
        and i + 2 < len(tokens)
        and tokens[i + 1] == "-m"
        and tokens[i + 2] in ("pip", "pip3")
    ):
        i += 3
    elif tokens[i] in ("pip", "pip3"):
        i += 1
    else:
        return False
    if i >= len(tokens) or tokens[i] != "install":
        return False
    i += 1
    # Walk the remaining args; package args are tokens that don't start with `-`.
    # A package spec may carry a version qualifier (`uv==0.4.0`, `uv>=1`, `uv[extra]`).
    for arg in tokens[i:]:
        if arg.startswith("-"):
            continue
        pkg = re.split(r"[<>=!~\[]", arg, maxsplit=1)[0]
        if pkg == "uv":
            return True
    return False


def _step_run_uses_uv(run_block: str) -> bool:
    """True if any logical line has `uv` as the first token of any pipeline segment.

    Detects `uv run`, `uv sync`, `cd langchain && uv export`, `make dev || uv run ...`.
    Does NOT detect `uvx`, `uv-helper`, comments, or absolute paths like
    `/usr/local/bin/uv`.
    """
    for _, line in _logical_lines(run_block):
        for segment in _PIPELINE_SPLIT.split(line):
            tokens = segment.split(None, 1)
            if tokens and tokens[0] == "uv":
                return True
    return False


def _step_label(step: Step) -> str:
    return (
        f"{step.workflow.relative_to(REPO_ROOT)}: job '{step.job}': "
        f"step {step.index} ({step.name})"
    )


def test_no_pip_install_uv_in_any_workflow_step() -> None:
    """Invariant 1: no `run:` step installs the `uv` package via pip.

    Walks each step's `run:` body, joins backslash-continued physical lines into
    logical lines, splits each logical line on shell command separators, and
    parses each segment as a candidate `pip install ...` invocation.
    """
    violations: list[str] = []
    for step in _iter_steps():
        if not step.run:
            continue
        for line_no, line in _logical_lines(step.run):
            for segment in _PIPELINE_SPLIT.split(line):
                if _is_pip_install_uv_command(segment):
                    violations.append(
                        f"{_step_label(step)}: line {line_no}: "
                        f"forbidden `pip install uv`: {segment.strip()!r}"
                    )
    assert not violations, (
        "Workflow steps install uv via pip — this is forbidden by the "
        "python-dependency-management spec. Use `astral-sh/setup-uv@<sha>` "
        "instead.\n  " + "\n  ".join(violations)
    )


def test_no_setup_python_in_same_job_as_uv() -> None:
    """Invariant 2: jobs that use uv MUST NOT also use actions/setup-python.

    "Uses uv" means: any step's `uses:` starts with `astral-sh/setup-uv@`, OR
    any step's `run:` (after logical-line joining + pipeline splitting) has
    `uv` as the first token of any segment — so `cd langchain && uv export`
    counts as uv usage.
    """
    by_job: dict[tuple[Path, str], list[Step]] = {}
    for step in _iter_steps():
        by_job.setdefault((step.workflow, step.job), []).append(step)

    violations: list[str] = []
    for (workflow, job), steps in by_job.items():
        uses_uv = any(
            s.uses.startswith("astral-sh/setup-uv@") or _step_run_uses_uv(s.run)
            for s in steps
        )
        setup_python = next(
            (s for s in steps if s.uses.startswith("actions/setup-python@")),
            None,
        )
        if uses_uv and setup_python is not None:
            violations.append(
                f"{workflow.relative_to(REPO_ROOT)}: job '{job}': uses uv "
                f"AND has `actions/setup-python@` step "
                f"(step {setup_python.index}, name={setup_python.name!r}). "
                f"setup-uv reads .python-version; setup-python is redundant."
            )
    assert not violations, (
        "Jobs pair `actions/setup-python` with uv usage — `setup-uv` is the "
        "single Python manager per job per the python-dependency-management "
        "spec.\n  " + "\n  ".join(violations)
    )


def test_pytest_uses_extra_test_not_with() -> None:
    """Invariant 3: pytest under `uv run` uses `--extra test`, never `--with`.

    Operates on logical lines (so a `uv run \\` continuation followed by
    `--with pytest pytest ...` on the next physical line is detected as one
    logical line). Gated on co-occurrence of `uv run` AND `pytest`, so
    non-pytest `--with` usage (e.g. `uvx pip-audit@2.10.0`) is unaffected.
    """
    violations: list[str] = []
    for step in _iter_steps():
        if not step.run:
            continue
        for line_no, line in _logical_lines(step.run):
            if "uv run" not in line or "pytest" not in line:
                continue
            problems = []
            if "--extra test" not in line:
                problems.append("missing `--extra test`")
            if "--with" in line:
                problems.append(
                    "uses forbidden `--with` (test deps come from the test extra)"
                )
            if problems:
                violations.append(
                    f"{_step_label(step)}: line {line_no}: {', '.join(problems)}: "
                    f"{line.strip()!r}"
                )
    assert not violations, (
        "Workflow steps run pytest via `uv run` without using `--extra test`, "
        "or using `--with`. Test deps come from the root pyproject.toml's test "
        "extra (single source of truth).\n  " + "\n  ".join(violations)
    )
