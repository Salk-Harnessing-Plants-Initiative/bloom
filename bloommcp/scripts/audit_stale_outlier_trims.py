"""One-time, read-only audit: pre-#420 silent `remove_outliers` reverts (bloom#585).

Before `fix-bloommcp-remove-outliers-tool-class` (#420) shipped, `remove_outliers`
persisted its trimmed run under the *shared* `qc` tool class. Any experiment where
the sequence `qc_clean -> remove_outliers -> qc_clean` happened before #420 merged
may have a silently-reverted trim sitting in that environment's bucket right now: a
`version="latest"` read under the old resolution order returns the *un-trimmed*
plain clean, with no error or warning that a trim was ever made.

This script scans every `qc_<stem>/manifest.json` in the configured storage backend
(or, with `--experiment`, only the named experiments' -- see `bloom_mcp.audit_scope`)
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
lift -- see `openspec/changes/archive/2026-08-09-add-bloommcp-outliers-staleness-audit/design.md`.

Each hit also carries `post_420_status` (`"not_remediated"`, `"remediated_and_current"`,
`"remediated_but_stale_again"`, or `"unknown"`): this legacy `qc_<stem>` manifest is
never written to again after #420, so without this annotation a hit is reported
identically forever, indistinguishable from "still exposed" from "already fixed" by a
later, post-#420 `remove_outliers` run against the separate `outliers_<stem>` manifest.
Computed via `trim_staleness` -- the same primitive `list_existing_analyses` surfaces.

Read-only: the core scan (`scan_for_stale_outlier_trims`) never imports or calls
`write_manifest`/`upload_file`/`write_json` -- it never touches a `qc_<stem>` or
`outliers_<stem>` manifest. The one write this script performs is its own report,
persisted as a new, timestamped, self-describing JSON object (including this
module's `SCOPE_NOTE` in the payload itself, and a random suffix in its key so
two runs finishing in the same second can't clobber each other) under a
dedicated `bloommcp_output/_audit_reports/` prefix (never overwriting any
experiment manifest) -- a deliberate, disclosed exception to "read-only," and
the one place this script's behavior extends beyond #585's literal
"report-only" ask (a durable artifact, not only stdout).

Run this against a real environment's bucket, not an empty local/dev one -- it
finds nothing meaningful otherwise. It uses the same storage configuration
(`SUPABASE_URL`, `BLOOM_AGENT_KEY`, `BLOOM_STORAGE_BACKEND`, etc.) the running
`bloommcp` service for that environment uses, so run it in that same environment
(e.g. `docker compose exec bloommcp uv run python scripts/audit_stale_outlier_trims.py`),
mirroring `tests/smoke/live_persistence_smoke.py`'s documented env-override
convention for host-vs-container invocation.

Usage: `cd bloommcp && uv run python scripts/audit_stale_outlier_trims.py
[--experiment IDENTIFIER ...]`. Without `--experiment` it sweeps the whole shared
`bloommcp_output/` root; with it, only the named experiments' prefixes are read
(bloom#919; identifier rules in `bloom_mcp.audit_scope`).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Optional

from bloom_mcp.audit_scope import (
    ExperimentScope,
    add_experiment_argument,
    all_requested_failed,
    experiment_scope_record,
    resolve_experiment_scope,
    summary_prefix,
)
from bloom_mcp.experiment_utils import (
    QC_TOOL_CLASS,
    REMOVE_OUTLIERS_TOOL_NAME,
    safe_error_text,
    trim_staleness,
)
from bloom_mcp.manifest import AnalysisDir
from bloom_mcp.storage_backend import active_backend_name
from bloom_mcp.supabase_client import list_prefix, write_json

_QC_PREFIX = f"{QC_TOOL_CLASS}_"
_OUTPUT_ROOT = "bloommcp_output"
_REPORT_PREFIX = f"{_OUTPUT_ROOT}/_audit_reports/"

# Persisted verbatim into every report (payload, not just this module's
# docstring) so a report read months later -- possibly pasted into a ticket
# with no memory of this script's source -- isn't misread as "fully clear"
# when it's actually silent about a real, narrower gap.
SCOPE_NOTE = (
    "Reports only experiments whose trim is CURRENTLY superseded (the "
    "manifest's current `latest` entry was authored by a tool other than "
    "remove_outliers). A manifest whose history shows "
    "qc_clean -> remove_outliers -> qc_clean -> remove_outliers, where the "
    "second remove_outliers is the current latest, is NOT reported -- even "
    "though a real, temporary exposure window existed between the two "
    "qc_clean commits. This is a current-state audit, not a full historical "
    "exposure-window reconstruction."
)


def scan_for_stale_outlier_trims(
    experiments: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Scan every `qc_<stem>` manifest (or only the requested experiments') and
    report silently-superseded trims.

    Returns `{"hits": [...], "errors": [...], "experiments_scanned": N}`.
    `experiments_scanned` counts every `qc_<stem>` prefix examined, regardless
    of whether it had a readable manifest.

    `experiments` (bloom#919) scopes the scan to those experiment identifiers:
    each `qc_<stem>/` prefix is resolved directly and the shared root is never
    listed. A scoped result also carries `unevaluated` -- requested experiments
    with no manifest (`"no_manifest"`) or no `latest` pointer (`"no_latest"`),
    which a full sweep skips silently but a scoped run must disclose. Invalid
    identifiers raise `ValueError` before any storage call.

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
    scope = resolve_experiment_scope(experiments)
    hits: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    unevaluated: list[dict[str, str]] = []
    experiments_scanned = 0

    if scope is None:
        names = list_prefix(f"{_OUTPUT_ROOT}/")
        stems = [
            name[len(_QC_PREFIX) :] for name in names if name.startswith(_QC_PREFIX)
        ]
    else:
        stems = [e.stem for e in scope]

    for stem in stems:
        experiments_scanned += 1
        try:
            manifest = AnalysisDir(
                _OUTPUT_ROOT, f"{stem}.csv", QC_TOOL_CLASS
            ).read_manifest()
        except (
            Exception
        ) as exc:  # noqa: BLE001 - best-effort forensic sweep, see docstring
            errors.append({"stem": stem, "error": safe_error_text(exc)})
            continue

        if manifest is None:
            # A qc_<stem> prefix with no manifest.json at all is a normal,
            # unremarkable state (a legacy un-versioned clean, or a commit
            # that uploaded outputs but never reached the manifest write) --
            # not a failure. A scoped run named this experiment, so it says so.
            if scope is not None:
                unevaluated.append({"stem": stem, "reason": "no_manifest"})
            continue
        if manifest.latest is None:
            # No current "latest" to compare a trim against.
            if scope is not None:
                unevaluated.append({"stem": stem, "reason": "no_latest"})
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
        if latest_entry.tool == REMOVE_OUTLIERS_TOOL_NAME:
            # The current latest is itself a trim -- still live, not a hit,
            # regardless of how many earlier remove_outliers entries exist
            # (issue #419's legitimate re-trim pattern).
            continue

        remove_outliers_entries = [
            v for v in manifest.versions if v.tool == REMOVE_OUTLIERS_TOOL_NAME
        ]
        if not remove_outliers_entries:
            continue

        # `created_at` is second-granularity (contract/provenance.py); two
        # remove_outliers commits within the same wall-clock second would tie
        # under a bare `max(..., key=created_at)`, and Python's max() keeps
        # the FIRST maximal element -- silently naming the earlier, wrong
        # entry as "superseded". manifest.versions is in commit/append order,
        # so break ties on position (higher index = more recent).
        superseded = max(
            enumerate(remove_outliers_entries),
            key=lambda pair: (pair[1].created_at, pair[0]),
        )[1]

        hits.append(
            {
                "stem": stem,
                "superseded_entry_id": superseded.id,
                "superseded_entry_created_at": superseded.created_at,
                "current_latest_id": latest_entry.id,
                "current_latest_tool": latest_entry.tool,
                "current_latest_created_at": latest_entry.created_at,
                "post_420_status": _post_420_status(stem),
            }
        )

    result: dict[str, Any] = {
        "hits": hits,
        "errors": errors,
        "experiments_scanned": experiments_scanned,
    }
    if scope is not None:
        result["unevaluated"] = unevaluated
    return result


def _post_420_status(stem: str) -> str:
    """Whether a historical hit has since been remediated by a post-#420
    `remove_outliers` run (which commits to the separate `outliers_<stem>`
    manifest this scan's legacy `qc_<stem>` read never looks at) -- without
    this, a hit is reported identically forever, indistinguishable from
    "still exposed" vs. "already fixed", even though `trim_staleness` (the
    same primitive `list_existing_analyses` uses) can answer that cheaply.

    One of `"not_remediated"` (no post-#420 `remove_outliers` run at all),
    `"remediated_and_current"`, `"remediated_but_stale_again"` (a fresh
    `qc_clean` ran after that later trim), or `"unknown"` (the check itself
    failed -- reported as a status, not silently swallowed into a crash of
    the whole scan).
    """
    try:
        result = trim_staleness(stem)
    except Exception:  # noqa: BLE001 - annotation-only, must not abort the scan
        return "unknown"
    if result is None:
        return "not_remediated"
    return "remediated_but_stale_again" if result.is_stale else "remediated_and_current"


def write_report(
    report: dict[str, Any], scope: Optional[ExperimentScope] = None
) -> str:
    """Persist `report` as a self-describing, timestamped JSON object.

    Adds `scanned_at` (ISO-8601 UTC), `storage_backend`, `scope_note`, and
    `experiment_scope` (which experiments this run covered -- `{"mode": "all"}`
    for a full sweep; bloom#919) to the payload itself (not only this
    module's docstring, and not only the object's key) so the report stays interpretable -- including its own
    detection-scope caveat -- if later moved, renamed, or copied elsewhere
    (e.g. pasted into a ticket with no memory of this script's source).
    Writes under a dedicated `_audit_reports/` prefix, distinct from any
    `qc_<stem>`/`outliers_<stem>` manifest -- this is the one write this
    script performs. The key includes a short random suffix (not just a
    per-second timestamp) so two runs completing in the same wall-clock
    second -- e.g. two engineers, or a retry after what looked like a hang --
    can't silently overwrite one another.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "scanned_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "storage_backend": active_backend_name(),
        "scope_note": SCOPE_NOTE,
        "experiment_scope": experiment_scope_record(scope),
        **report,
    }
    suffix = uuid.uuid4().hex[:8]
    key = (
        f"{_REPORT_PREFIX}stale_outlier_trims_"
        f"{now.strftime('%Y%m%dT%H%M%SZ')}_{suffix}.json"
    )
    write_json(key, payload)
    return key


def run(argv: Sequence[str] = ()) -> int:
    """Parse `argv`, scan, persist the report, print it, and return an exit code.

    Parses only `argv` (never `sys.argv`; `main()` passes that). Returns `2` for
    an invalid `--experiment` value, before any storage access. Returns `1` when
    the scan couldn't run at all (enumeration failed, or a scoped run couldn't
    read a single requested experiment -- nothing to report, nothing persisted).
    Returns `0` whenever the scan completes, including when it reports hits
    and/or per-stem errors: those are the script's normal, successful output,
    not a script failure.
    """
    parser = argparse.ArgumentParser(
        prog="audit_stale_outlier_trims.py",
        description="Read-only audit of pre-#420 silent remove_outliers reverts.",
    )
    add_experiment_argument(parser)
    args = parser.parse_args(list(argv))
    try:
        scope = resolve_experiment_scope(args.experiments)
    except (TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        report = scan_for_stale_outlier_trims(args.experiments)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - top-level failure, reported then exits non-zero
        print(
            f"error: could not enumerate manifests: {safe_error_text(exc)}",
            file=sys.stderr,
        )
        return 1

    if all_requested_failed(report, scope):
        print(json.dumps(report, indent=2))
        print(
            "error: no requested experiment could be read; no report persisted",
            file=sys.stderr,
        )
        for error in report["errors"]:
            print(f"  {error['stem']}: {error['error']}", file=sys.stderr)
        return 1

    key = write_report(report, scope)
    print(json.dumps(report, indent=2))
    print(f"scope: {SCOPE_NOTE}")
    print(
        f"{summary_prefix(scope)}"
        f"{report['experiments_scanned']} experiments scanned, "
        f"{len(report['hits'])} hits, {len(report['errors'])} errors, "
        f"report written to {key}"
    )
    return 0


def main() -> None:
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
