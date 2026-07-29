"""Shared batch-result reporting for `cyl batch-*` commands.

`ScanResult`/`BatchResult` mirror `sleap_roots_predict.batch.ScanResult`/`BatchResult`'s shape
(``status``: ``ok``/``skipped``/``failed``) rather than
`trait_extractor.extractor.BatchResult`'s ``succeeded``/``failed``-list shape, since
both new commands need to represent a skip (write-back's RPC ``was_noop``; stage-in's resume).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

Status = Literal["ok", "skipped", "failed"]
_VALID_STATUSES: frozenset[str] = frozenset({"ok", "skipped", "failed"})


@dataclass
class ScanResult:
    """Outcome of staging or ingesting one item in a batch.

    Validates ``status`` at construction time (not just via the ``Literal`` type hint, which
    nothing enforces at runtime since this package doesn't run mypy in CI) — a typo'd status
    string would otherwise silently fail to count as a failure anywhere that checks
    ``status == "failed"``/``!= "failed"``, per a PR review finding.
    """

    scan_key: str
    status: Status
    error: str = ""

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(
                f"invalid ScanResult status {self.status!r}; must be one of "
                f"{sorted(_VALID_STATUSES)}"
            )


@dataclass
class BatchResult:
    """Aggregate outcome of a batch run."""

    scans: list[ScanResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True iff no scan failed (skipped/ok scans are fine)."""
        return all(s.status in ("ok", "skipped") for s in self.scans)


def format_summary(result: BatchResult, *, verb: str, noun: str, destination: str) -> str:
    """Human-readable summary line plus one line per failed item."""
    total = len(result.scans)
    ok = sum(1 for s in result.scans if s.status == "ok")
    skipped = sum(1 for s in result.scans if s.status == "skipped")
    failed = [s for s in result.scans if s.status == "failed"]

    plural = "" if total == 1 else "s"
    header = f"{verb} {ok}/{total} {noun}{plural} -> {destination}"
    if skipped:
        header += f"  ({skipped} skipped)"
    if failed:
        header += f"  ({len(failed)} failed)"

    lines = [header]
    for s in failed:
        lines.append(f"FAILED {s.scan_key}: {s.error}")
    return "\n".join(lines)


def format_json(result: BatchResult) -> str:
    """The aggregate batch result as a JSON array, one object per item."""
    return json.dumps(
        [{"scan_key": s.scan_key, "status": s.status, "error": s.error} for s in result.scans]
    )
