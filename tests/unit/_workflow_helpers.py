"""Shared helpers for parsing ``.github/workflows/*.yml`` in unit tests.

Extracted from ``test_ci_workflow_uv_conventions.py`` so multiple workflow-shape
guards (e.g. ``test_bloommcp_wheel_import_gate.py``) can reuse the logical-line
joiner without a test-module-imports-test-module coupling.
"""

from __future__ import annotations

from typing import Iterator


def _strip_line_comment(line: str) -> str:
    """Return the line with anything from an unquoted ``#`` onward removed.

    Quoting in shell is permissive — we only care about defeating trivial
    comment-out patterns like ``# pip install uv``. A character-by-character
    scan that respects single/double quotes is sufficient for the workflow
    files we audit.
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


def _logical_lines(run_block: str) -> Iterator[tuple[int, str]]:
    """Yield (starting-physical-line-number, joined-logical-line) pairs.

    Physical lines connected by a trailing backslash (``\\``) are joined into one
    logical line, matching shell continuation semantics. Comments are stripped
    before the backslash check, so ``cmd \\  # comment`` continues correctly.
    The starting physical line number is preserved so failure messages still
    point at the right place.
    """
    physical = run_block.splitlines()
    i = 0
    while i < len(physical):
        start = i + 1  # 1-based for human-readable error messages
        accumulated: list[str] = []
        while i < len(physical):
            stripped = _strip_line_comment(physical[i]).rstrip()
            if stripped.endswith("\\"):
                accumulated.append(stripped[:-1].rstrip())
                i += 1
                continue
            accumulated.append(stripped)
            i += 1
            break
        joined = " ".join(seg.strip() for seg in accumulated if seg.strip())
        if joined:
            yield start, joined
