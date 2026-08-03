"""One-time, read-only audit: pre-#420 silent `remove_outliers` reverts (bloom#585).

Before `fix-bloommcp-remove-outliers-tool-class` (#420) shipped, `remove_outliers`
persisted its trimmed run under the *shared* `qc` tool class. Any experiment where
the sequence `qc_clean -> remove_outliers -> qc_clean` happened before #420 merged
may have a silently-reverted trim sitting in that environment's bucket right now: a
`version="latest"` read under the old resolution order returns the *un-trimmed*
plain clean, with no error or warning that a trim was ever made.

This script scans every `qc_<stem>/manifest.json` in the configured storage backend
and reports each experiment where a `remove_outliers`-authored version exists in
that manifest's history but the manifest's *current* `latest` entry was authored by
a different tool -- exactly that pattern. A `remove_outliers`-authored entry that is
superseded only by a *later* `remove_outliers`-authored entry (a legitimate re-trim,
e.g. re-running with a different method after a poor fit -- see issue #419) is not
reported: the current trim is still live, nothing was silently reverted.

Scope: this reports the manifest's *current* state only, matching #585's own literal
ask ("not that manifest's current latest pointer"). A manifest whose history
contains `qc_clean -> remove_outliers -> qc_clean -> remove_outliers`, where the
second `remove_outliers` is the current latest, is not a hit -- even though a real,
temporary exposure window existed between the two `qc_clean` commits. Reconstructing
every historical exposure window a manifest ever passed through (as opposed to
reporting experiments whose trim is *currently* superseded) is a heavier, unscoped
lift -- see `openspec/changes/add-bloommcp-outliers-staleness-audit/design.md`.

Read-only: the core scan (`scan_for_stale_outlier_trims`) never imports or calls
`write_manifest`/`upload_file`/`write_json` -- it never touches a `qc_<stem>` or
`outliers_<stem>` manifest. The one write this script performs is its own report,
persisted as a new, timestamped, self-describing JSON object under a dedicated
`bloommcp_output/_audit_reports/` prefix (never overwriting any experiment
manifest) -- a deliberate, disclosed exception to "read-only," and the one place
this script's behavior extends beyond #585's literal "report-only" ask (a durable
artifact, not only stdout).

Run this against a real environment's bucket, not an empty local/dev one -- it
finds nothing meaningful otherwise. It uses the same storage configuration
(`SUPABASE_URL`, `BLOOM_AGENT_KEY`, `BLOOM_STORAGE_BACKEND`, etc.) the running
`bloommcp` service for that environment uses, so run it in that same environment
(e.g. `docker compose exec bloommcp uv run python scripts/audit_stale_outlier_trims.py`),
mirroring `tests/smoke/live_persistence_smoke.py`'s documented env-override
convention for host-vs-container invocation.

Usage: `cd bloommcp && uv run python scripts/audit_stale_outlier_trims.py`
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from bloom_mcp.experiment_utils import QC_TOOL_CLASS
from bloom_mcp.manifest import AnalysisDir
from bloom_mcp.storage_backend import active_backend_name
from bloom_mcp.supabase_client import list_prefix, write_json

_QC_PREFIX = f"{QC_TOOL_CLASS}_"
_OUTPUT_ROOT = "bloommcp_output"
_REPORT_PREFIX = f"{_OUTPUT_ROOT}/_audit_reports/"


def scan_for_stale_outlier_trims() -> dict[str, Any]:
    """Scan every `qc_<stem>` manifest and report silently-superseded trims.

    Returns `{"hits": [...], "errors": [...], "experiments_scanned": N}`.
    `experiments_scanned` counts every `qc_<stem>` prefix examined, regardless
    of whether it had a readable manifest.

    Enumeration (`list_prefix`) is unguarded: if the environment is unreachable
    or misconfigured, there is nothing to report at all, so this propagates
    rather than returning an empty, misleadingly "successful" scan. A failure
    reading one specific stem's manifest (malformed JSON, an unsupported
    schema version, a field-validation failure, or a storage/network error)
    is caught per-stem and recorded in `errors`; the scan continues to the
    next stem -- a corrupt manifest for one experiment must not hide every
    other experiment's result in a one-shot forensic sweep over a
    potentially large bucket.
    """
    hits: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    experiments_scanned = 0

    names = list_prefix(f"{_OUTPUT_ROOT}/")
    stems = [name[len(_QC_PREFIX) :] for name in names if name.startswith(_QC_PREFIX)]

    for stem in stems:
        experiments_scanned += 1
        try:
            manifest = AnalysisDir(
                _OUTPUT_ROOT, f"{stem}.csv", QC_TOOL_CLASS
            ).read_manifest()
        except (
            Exception
        ) as exc:  # noqa: BLE001 - best-effort forensic sweep, see docstring
            errors.append({"stem": stem, "error": str(exc)})
            continue

        if manifest is None:
            # A qc_<stem> prefix with no manifest.json at all is a normal,
            # unremarkable state (a legacy un-versioned clean, or a commit
            # that uploaded outputs but never reached the manifest write) --
            # not a failure.
            continue
        if manifest.latest is None:
            # No current "latest" to compare a trim against.
            continue

        latest_entry = next(
            (v for v in manifest.versions if v.id == manifest.latest), None
        )
        if latest_entry is None:
            errors.append(
                {
                    "stem": stem,
                    "error": (
                        f"manifest.latest={manifest.latest!r} has no matching "
                        "VersionEntry in manifest.versions"
                    ),
                }
            )
            continue
        if latest_entry.tool == "remove_outliers":
            # The current latest is itself a trim -- still live, not a hit,
            # regardless of how many earlier remove_outliers entries exist
            # (issue #419's legitimate re-trim pattern).
            continue

        remove_outliers_entries = [
            v for v in manifest.versions if v.tool == "remove_outliers"
        ]
        if not remove_outliers_entries:
            continue

        superseded = max(remove_outliers_entries, key=lambda v: v.created_at)
        hits.append(
            {
                "stem": stem,
                "superseded_entry_id": superseded.id,
                "superseded_entry_created_at": superseded.created_at,
                "current_latest_id": latest_entry.id,
                "current_latest_tool": latest_entry.tool,
                "current_latest_created_at": latest_entry.created_at,
            }
        )

    return {
        "hits": hits,
        "errors": errors,
        "experiments_scanned": experiments_scanned,
    }


def write_report(report: dict[str, Any]) -> str:
    """Persist `report` as a self-describing, timestamped JSON object.

    Adds `scanned_at` (ISO-8601 UTC) and `storage_backend` to the payload
    itself (not only the object's key) so the report stays interpretable if
    later moved, renamed, or copied elsewhere. Writes under a dedicated
    `_audit_reports/` prefix, distinct from any `qc_<stem>`/`outliers_<stem>`
    manifest -- this is the one write this script performs.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "scanned_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "storage_backend": active_backend_name(),
        **report,
    }
    key = f"{_REPORT_PREFIX}stale_outlier_trims_{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    write_json(key, payload)
    return key


def run() -> int:
    """Scan, persist the report, print it, and return an exit code.

    Returns `1` only when the scan couldn't run at all (enumeration failed --
    nothing to report). Returns `0` whenever the scan completes, including
    when it reports hits and/or per-stem errors: those are the script's
    normal, successful output, not a script failure.
    """
    try:
        report = scan_for_stale_outlier_trims()
    except (
        Exception
    ) as exc:  # noqa: BLE001 - top-level failure, reported then exits non-zero
        print(f"error: could not enumerate manifests: {exc}", file=sys.stderr)
        return 1

    key = write_report(report)
    print(json.dumps(report, indent=2))
    print(
        f"{report['experiments_scanned']} experiments scanned, "
        f"{len(report['hits'])} hits, {len(report['errors'])} errors, "
        f"report written to {key}"
    )
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
