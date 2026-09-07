"""One dated JSON report per run, written to Box beside the mirrored objects.

The ledger's `runs` table already records what each run did, but it lives in
`/var/lib` on the deploy host — so answering "did the backup run last week?"
means having SSH and knowing SQLite. The mirror itself cannot answer it
either: it holds current state, and a week where nothing changed looks exactly
like a week where nothing ran.

So each run drops a small dated file under `<box_root>/_runs/`. A missing week
is then visible in a Box folder listing, by anyone, without server access. The
files are a few KB against a ~7TB mirror.

Reports are written even when the run fails — a failed run is precisely the one
worth having a record of.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

# Folder under the object root that holds the reports. Leading underscore so
# it sorts away from the mirrored bucket folders.
REPORTS_DIRNAME = "_runs"

# The ledger is uploaded beside the reports rather than among the objects: it
# is not a backed-up object, and a folder of its own keeps it out of any
# restore that walks the mirror.
STATE_DIRNAME = "_state"
LEDGER_FILENAME = "ledger.db"

# A seed run can fail on thousands of objects; the report names enough to act
# on and records that it truncated rather than growing without bound.
MAX_REPORTED_FAILURES = 200


@dataclass
class RunReport:
    """What one run did, in the form that lands on Box."""

    env: str
    run_id: int
    started_at: datetime
    finished_at: datetime
    outcome: str
    box_root: str
    # Where the bytes came FROM. The restore procedure needs both to rebuild a
    # MinIO key, and said they were "recorded in every run report" — they were
    # not. They live in .env.prod on the deploy host, which is the machine a
    # restore assumes is gone.
    minio_bucket: str = ""
    minio_prefix: str = ""
    stats: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    # Objects that are NOT on Box by the two routes that are not a copy
    # failure: refused before the attempt because Box cannot store the name,
    # and found missing by verification after the copy reported success. A
    # count alone left the identity of a missing object nowhere but a GitHub
    # Actions log under retention — and this file exists precisely because
    # /var/lib and a rotating log cannot answer "which one?" later.
    skips: list[str] = field(default_factory=list)
    # Objects Box cannot store BY NAME, listed separately from `skips`.
    # Sharing one capped list meant collisions — which are recoverable by a
    # rename either side — crowded out the name-skips, which are not: the
    # watermark advances past them, so this file is the only record that they
    # exist. 300 collisions ahead of 50 bad names left 0 of the 50 named.
    name_skips: list[str] = field(default_factory=list)
    verify_failures: list[str] = field(default_factory=list)
    # The run's own verdict, in the same closed vocabulary the workflow
    # branches on. It is here as well as in the log because the log travels
    # back over an ssh pipe the workflow holds open, and a cancelled or
    # timed-out job kills that pipe before the verdict is printed — on exactly
    # the runs most worth explaining. This file is written on the host first,
    # so it survives the connection dying.
    status: str = ""
    # The flags known when the report was written. The two ledger flags are
    # set by an upload that has not run yet, so they are never here; they
    # reach a human through the exit code instead.
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        listed = self.failures[:MAX_REPORTED_FAILURES]
        # The copier stops collecting paths past its own ceiling, so the list
        # cannot be trusted for the total — `stats["failed"]` counts them all.
        total_failures = self.stats.get("failed", len(self.failures))
        skips = self.skips[:MAX_REPORTED_FAILURES]
        total_skips = self.stats.get("skipped", len(self.skips))
        return {
            "schema": SCHEMA_VERSION,
            "env": self.env,
            "run_id": self.run_id,
            "outcome": self.outcome,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "duration_seconds": round(
                (self.finished_at - self.started_at).total_seconds(), 1
            ),
            "box_root": self.box_root,
            "minio_bucket": self.minio_bucket,
            "minio_prefix": self.minio_prefix,
            "stats": dict(self.stats),
            "failures": listed,
            "failures_truncated": total_failures > len(listed),
            "failure_count": total_failures,
            # Names, not just counts. `stats["skipped"]` and
            # `stats["verify_mismatched"]` remain the exact totals.
            "status": self.status,
            "flags": list(self.flags),
            "skips": skips,
            "name_skips": self.name_skips[:MAX_REPORTED_FAILURES],
            "name_skips_truncated": len(self.name_skips) > MAX_REPORTED_FAILURES,
            "skips_truncated": total_skips > len(skips),
            "verify_failures": self.verify_failures[:MAX_REPORTED_FAILURES],
        }

    def to_json(self) -> str:
        # Indented so the file is readable in Box's own preview pane.
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    def filename(self) -> str:
        """Sortable, and free of every character Box rejects.

        `:` is in BOX_ILLEGAL_CHARS, so the usual ISO timestamp cannot be used
        verbatim — the time is compacted instead of separated.
        """
        stamp = self.started_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        return f"{stamp}-{self.env}-run{self.run_id:05d}.json"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def write_local(report: RunReport, state_dir: Path | str) -> Path:
    """Write the report next to the ledger, and return its path.

    Kept on the host as well as Box: if the upload is what fails, the record
    still exists somewhere.
    """
    directory = Path(state_dir) / REPORTS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / report.filename()
    path.write_text(report.to_json(), encoding="utf-8")
    return path


def box_ledger_path(box_root: str) -> str:
    """Destination for the ledger copy, under the run's Box root."""
    root = box_root.strip("/")
    parts = [part for part in (root, STATE_DIRNAME, LEDGER_FILENAME) if part]
    return "/".join(parts)


def box_remote_path(report: RunReport) -> str:
    """Destination path for the report, under the run's Box root."""
    root = report.box_root.strip("/")
    parts = [part for part in (root, REPORTS_DIRNAME, report.filename()) if part]
    return "/".join(parts)
