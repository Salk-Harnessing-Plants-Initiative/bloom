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
    # Where the bytes came from. A restore needs both to rebuild a MinIO key.
    minio_bucket: str = ""
    minio_prefix: str = ""
    stats: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    # Not on Box for a reason that is not a copy failure: an unstorable name,
    # or missing when verification looked.
    skips: list[str] = field(default_factory=list)
    # Names Box cannot store. Their own capped list, so a flood of collisions
    # cannot crowd out the ones nothing can fix.
    name_skips: list[str] = field(default_factory=list)
    # Rows Postgres lists whose bytes are not in MinIO. Nothing here can fix
    # one; someone has to decide whether the row should exist.
    source_gone: list[str] = field(default_factory=list)
    verify_failures: list[str] = field(default_factory=list)
    # The run's verdict, in the vocabulary the workflow branches on. Written on
    # the host, so it survives the ssh pipe dying.
    # The GitHub job that started this run, empty when a person did. The
    # workflow finds this night's report by matching it, which is why the
    # report carries it as well as the lock: the lock is released and blanked
    # when the run ends, and the summary looks afterwards.
    actions_run: str = ""
    status: str = ""
    # Flags known at write time. The two ledger flags are set later, so they
    # never appear here — the exit code carries them instead.
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        listed = self.failures[:MAX_REPORTED_FAILURES]
        # The copier stops collecting paths at its ceiling; stats holds the total.
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
            "actions_run": self.actions_run,
            "status": self.status,
            "flags": list(self.flags),
            "skips": skips,
            "name_skips": self.name_skips[:MAX_REPORTED_FAILURES],
            "name_skips_truncated": len(self.name_skips) > MAX_REPORTED_FAILURES,
            "source_gone": self.source_gone[:MAX_REPORTED_FAILURES],
            "source_gone_truncated": len(self.source_gone) > MAX_REPORTED_FAILURES,
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


def find_local(state_dir: Path | str, actions_run: str) -> Path | None:
    """The newest report on this host written by a given GitHub job.

    The workflow calls this over ssh when a cancel or a timeout killed the log
    before the run printed its verdict. Matching the job id rather than a time
    window is what keeps a nightly from picking up the seed's report: the seed
    is started by hand and carries no job id at all, so it can never match.

    Filenames sort chronologically, so the last match is the newest.
    """
    if not actions_run:
        return None
    directory = Path(state_dir) / REPORTS_DIRNAME
    try:
        candidates = sorted(directory.glob("*.json"))
    except OSError:
        return None
    for path in reversed(candidates):
        try:
            body = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(body, dict) and body.get("actions_run") == actions_run:
            return path
    return None


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


def main(argv: list[str] | None = None) -> int:
    """Print this job's own run report, for the workflow's summary step."""
    import argparse

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--actions-run", required=True)
    args = parser.parse_args(argv)
    found = find_local(args.state_dir, args.actions_run)
    if found is None:
        return 1
    print(found.read_text(encoding="utf-8", errors="replace"), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
