"""Regression-guard test for CI workflow uv conventions.

Enforces requirements from openspec/changes/harden-ci-uv-conventions/specs/
python-dependency-management/spec.md (composes with pin-python-deps in
openspec/specs/python-dependency-management/spec.md once #160 archives).

Three invariants on every .github/workflows/*.{yml,yaml} file:
  1. No `run:` step installs uv via pip (`pip install uv`).
  2. No job that uses uv also has an `actions/setup-python@` step.
  3. Any `run:` line containing `uv run` AND `pytest` MUST use `--extra test`
     and MUST NOT contain `--with`.

Scope cut: top-level workflow files only. Composite actions and reusable
workflows referenced via `uses: ./...` or `uses: org/repo/...` are not
traversed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, NamedTuple

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"


class Step(NamedTuple):
    workflow: Path
    job: str
    index: int  # zero-based position in the job's `steps:` list
    name: str  # step `name:` or "<unnamed>"
    run: str  # `run:` body or ""
    uses: str  # `uses:` value or ""


def _strip_line_comment(line: str) -> str:
    """Return the line with anything from an unquoted `#` onward removed.

    Quoting in shell is permissive — we only care about defeating trivial
    comment-out patterns like `# pip install uv`. A character-by-character
    scan that respects single/double quotes is sufficient for the workflow
    files we audit (none use exotic shell quoting in their `run:` bodies).
    """
    out: list[str] = []
    quote: str | None = None
    for ch in line:
        if quote is None:
            if ch == "#":
                break
            if ch in ("'", '"'):
                quote = ch
        elif ch == quote:
            quote = None
        out.append(ch)
    return "".join(out)


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


# Token-aware: matches `uv` only when not preceded/followed by a word char
# or hyphen (so `uv-helper`, `pyuv`, `uvx` do NOT match).
_UV_TOKEN = re.compile(r"(?<![\w-])uv(?![\w-])")


def _is_pip_install_uv_line(line: str) -> bool:
    """True if the line installs the package `uv` (the standalone token) via pip."""
    stripped = _strip_line_comment(line).strip()
    if not stripped:
        return False
    # Must contain `pip` AND `install` as standalone tokens, AND `uv` as a
    # standalone token. Matches: `pip install uv`, `python -m pip install --upgrade uv`,
    # `pip3 install uv==0.4.0`. Does NOT match: `pip install uv-helper`, `pip install pyuv`.
    if not re.search(r"(?<![\w-])pip3?(?![\w-])", stripped):
        return False
    if not re.search(r"(?<![\w-])install(?![\w-])", stripped):
        return False
    return bool(_UV_TOKEN.search(stripped))


def _has_uv_first_token(run_block: str) -> bool:
    """True if any non-comment line in `run_block` has `uv` (exact token) as its first token.

    Excludes `uvx`, `uv-helper`, comments, and absolute paths like `/usr/local/bin/uv`.
    """
    for raw_line in run_block.splitlines():
        line = _strip_line_comment(raw_line).strip()
        if not line:
            continue
        # Split on whitespace; first token must be exactly "uv".
        first = line.split(None, 1)[0]
        if first == "uv":
            return True
    return False


def _step_label(step: Step) -> str:
    return (
        f"{step.workflow.relative_to(REPO_ROOT)}: job '{step.job}': "
        f"step {step.index} ({step.name})"
    )


def test_no_pip_install_uv_in_any_workflow_step() -> None:
    """Invariant 1: no `run:` step installs uv via pip."""
    violations: list[str] = []
    for step in _iter_steps():
        if not step.run:
            continue
        for line_no, line in enumerate(step.run.splitlines(), start=1):
            if _is_pip_install_uv_line(line):
                violations.append(
                    f"{_step_label(step)}: line {line_no}: "
                    f"forbidden `pip install uv` pattern: {line.strip()!r}"
                )
    assert not violations, (
        "Workflow steps install uv via pip — this is forbidden by the "
        "python-dependency-management spec. Use `astral-sh/setup-uv@<sha>` "
        "instead.\n  " + "\n  ".join(violations)
    )


def test_no_setup_python_adjacent_to_uv() -> None:
    """Invariant 2: jobs that use uv MUST NOT also use actions/setup-python."""
    # Group steps by (workflow, job).
    by_job: dict[tuple[Path, str], list[Step]] = {}
    for step in _iter_steps():
        by_job.setdefault((step.workflow, step.job), []).append(step)

    violations: list[str] = []
    for (workflow, job), steps in by_job.items():
        uses_uv = any(
            s.uses.startswith("astral-sh/setup-uv@") or _has_uv_first_token(s.run)
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
    """Invariant 3: pytest under `uv run` uses `--extra test`, never `--with`."""
    violations: list[str] = []
    for step in _iter_steps():
        if not step.run:
            continue
        for line_no, raw_line in enumerate(step.run.splitlines(), start=1):
            line = _strip_line_comment(raw_line)
            if "uv run" not in line or "pytest" not in line:
                continue
            problems = []
            if "--extra test" not in line:
                problems.append("missing `--extra test`")
            if "--with" in line:
                problems.append("uses forbidden `--with` (test deps come from the test extra)")
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
