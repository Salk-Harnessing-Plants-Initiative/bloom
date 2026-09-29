"""Experiment filter shared by the outlier audit scripts (bloom#919).

`bloommcp_output/` is one flat root shared by every user, so an un-scoped audit
lists every tenant's `<tool_class>_<stem>/` directories just to answer a question
about one experiment. `--experiment IDENTIFIER` (repeatable) lets an audit resolve
the named experiments' prefixes directly and never list that root.

Identifier rules (the one place they are documented; each script points here):

- Pass the same experiment identifier the MCP tools received. Its stem is
  `Path(identifier).stem`, exactly as `AnalysisDir` derives it, so the audit reads
  the prefix a tool actually wrote.
- A dotted stem copied from an earlier report (e.g. `exp.v2`) is passed as
  `exp.v2.csv` -- the same `f"{stem}.csv"` a full sweep rebuilds `AnalysisDir`
  from. Each report entry records `requested` next to the resolved `stem`.
- Values containing `/`, `\\` or NUL, empty/whitespace-only values, and values
  whose stem is empty, `.` or `..` are rejected before any storage call. The check
  runs on the raw value: `Path("../x.csv").stem` is `x`, so a stem-only check
  would let traversal through.
- Values resolving to the same stem collapse to the first one seen.

Pure (no storage I/O): each script keeps its own root enumeration so its tests'
patches on that script's `list_prefix` binding stay meaningful.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_FORBIDDEN_CHARS = ("/", "\\", "\x00")


@dataclass(frozen=True)
class ScopedExperiment:
    """One requested experiment: the value as given, and its resolved stem."""

    requested: str
    stem: str


ExperimentScope = tuple[ScopedExperiment, ...]


def resolve_experiment_scope(
    experiments: Optional[Sequence[str]],
) -> Optional[ExperimentScope]:
    """Validate and normalize an experiment filter.

    Returns `None` for a full sweep (`experiments is None`). Raises `ValueError`
    for an empty sequence or any invalid value (see the module docstring), and
    `TypeError` when handed a bare string instead of a sequence of them.
    """
    if experiments is None:
        return None
    if isinstance(experiments, str):
        raise TypeError("experiments must be a sequence of identifiers, not a string")
    if len(experiments) == 0:
        raise ValueError(
            "empty experiment filter; pass None (no --experiment) for a full sweep"
        )

    resolved: list[ScopedExperiment] = []
    seen: set[str] = set()
    for value in experiments:
        stem = _stem_or_raise(value)
        if stem in seen:
            continue
        seen.add(stem)
        resolved.append(ScopedExperiment(requested=value, stem=stem))
    return tuple(resolved)


def _stem_or_raise(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"experiment identifier must be a string, got {value!r}")
    if not value.strip():
        raise ValueError(f"empty experiment identifier: {value!r}")
    if any(ch in value for ch in _FORBIDDEN_CHARS):
        raise ValueError(
            f"experiment identifier must not contain a path separator or NUL: {value!r}"
        )
    stem = Path(value).stem
    if stem in ("", ".", ".."):
        raise ValueError(f"experiment identifier has no usable stem: {value!r}")
    return stem


def experiment_scope_record(scope: Optional[ExperimentScope]) -> dict[str, Any]:
    """The persisted report's `experiment_scope` field."""
    if scope is None:
        return {"mode": "all"}
    return {
        "mode": "experiments",
        "experiments": [{"requested": e.requested, "stem": e.stem} for e in scope],
    }


def summary_prefix(scope: Optional[ExperimentScope]) -> str:
    """Prefix for a script's printed summary line; empty for a full sweep."""
    if scope is None:
        return ""
    return f"scoped to {len(scope)} experiment(s): "


def add_experiment_argument(parser: argparse.ArgumentParser) -> None:
    """Register the shared, repeatable `--experiment IDENTIFIER` flag."""
    parser.add_argument(
        "--experiment",
        dest="experiments",
        action="append",
        metavar="IDENTIFIER",
        help=(
            "Audit only this experiment (repeatable); never lists the shared "
            "bloommcp_output/ root. See bloom_mcp.audit_scope for identifier rules."
        ),
    )
