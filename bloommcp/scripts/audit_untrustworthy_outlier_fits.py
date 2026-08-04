"""One-time, read-only audit: pre-#419 untrustworthy-fit `remove_outliers` trims (bloom#593).

Before `fix-bloommcp-remove-outliers-fit-gate` (#419) shipped, `remove_outliers`
persisted a mahalanobis trim regardless of whether the chi-squared distributional
assumption actually held for the data — `fit_is_trustworthy` was surfaced only as
an advisory field in the tool's return value, computed *after* the trim had
already committed as the experiment's new canonical "latest cleaned" version.
Any experiment trimmed before #419 shipped, whose fit was untrustworthy
(`goodness_of_fit.fit_quality` of `"poor"`, `"very_poor"`, or `"unknown"`) at
commit time, may have that same untrustworthy trim sitting as its canonical
cleaned version right now, with nothing distinguishing it from a trustworthy one.

This script scans every `outliers_<stem>/manifest.json` in the configured storage
backend and reports each experiment whose current `latest` entry is
`remove_outliers`-authored and whose persisted `outlier_report.json` records an
untrustworthy fit — exactly the run #419's live gate would now reject before
persisting.

Scope: this scans `outliers_<stem>` manifests only — the tool class `remove_outliers`
has written to since `fix-bloommcp-remove-outliers-tool-class` (#420). It does
**not** additionally scan legacy `qc_<stem>` manifests for a never-superseded,
pre-#420 `remove_outliers` entry that still happens to be that manifest's `latest`
(a `qc_clean` -> `remove_outliers` sequence, before #420 shipped, with no
`qc_clean` or post-#420 `remove_outliers` re-run since — so no `outliers_<stem>`
manifest was ever created for it at all). This is a real, disclosed, narrower edge
case — see `openspec/changes/add-bloommcp-outliers-fit-audit/design.md` Decision 2
for the full reasoning and the recommended follow-up (extending `#585`'s own
`qc_`-scanning script, which already identifies this exact subset, rather than
duplicating its scan/error-handling machinery here).

Read-only: the core scan (`scan_for_untrustworthy_outlier_fits`) never imports or
calls `write_manifest`/`upload_file`/`write_json` against any experiment manifest.
The one write this script performs is its own report, persisted as a new,
timestamped, self-describing JSON object (including this module's `SCOPE_NOTE` in
the payload itself, and a random suffix in its key so two runs finishing in the
same second can't clobber each other) under the same dedicated
`bloommcp_output/_audit_reports/` prefix `audit_stale_outlier_trims.py` (bloom#585)
already established — a distinct filename prefix keeps the two scripts' reports
from ever colliding.

Run this against a real environment's bucket, not an empty local/dev one — it
finds nothing meaningful otherwise. It uses the same storage configuration
(`SUPABASE_URL`, `BLOOM_AGENT_KEY`, `BLOOM_STORAGE_BACKEND`, etc.) the running
`bloommcp` service for that environment uses, so run it in that same environment
(e.g. `docker compose exec bloommcp uv run python scripts/audit_untrustworthy_outlier_fits.py`),
mirroring `tests/smoke/live_persistence_smoke.py`'s documented env-override
convention for host-vs-container invocation.

Usage: `cd bloommcp && uv run python scripts/audit_untrustworthy_outlier_fits.py`
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from bloom_mcp.experiment_utils import (
    OUTLIER_REPORT_NAME,
    OUTLIERS_TOOL_CLASS,
    REMOVE_OUTLIERS_TOOL_NAME,
    fit_is_trustworthy,
    safe_error_text,
)
from bloom_mcp.manifest import AnalysisDir
from bloom_mcp.storage_backend import active_backend_name
from bloom_mcp.supabase_client import list_prefix, read_json, write_json

_OUTPUT_ROOT = "bloommcp_output"
_OUTLIERS_PREFIX = f"{OUTLIERS_TOOL_CLASS}_"
_REPORT_PREFIX = f"{_OUTPUT_ROOT}/_audit_reports/"

# Persisted verbatim into every report (payload, not just this module's
# docstring) so a report read months later isn't misread as "fully clear" when
# it's actually silent about a real, narrower gap.
SCOPE_NOTE = (
    "Reports only experiments whose CURRENT latest outliers_<stem> entry has an "
    "untrustworthy recorded fit. Does NOT scan legacy qc_<stem> manifests for a "
    "never-superseded pre-#420 remove_outliers entry with no outliers_<stem> "
    "manifest at all -- see design.md Decision 2 (add-bloommcp-outliers-fit-audit)."
)


def scan_for_untrustworthy_outlier_fits() -> dict[str, Any]:
    """Scan every `outliers_<stem>` manifest and report untrustworthy-fit hits.

    Returns `{"hits": [...], "errors": [...], "experiments_scanned": N}`.
    `experiments_scanned` counts every `outliers_<stem>` prefix examined,
    regardless of whether it had a readable manifest.

    Enumeration (`list_prefix`) is unguarded: if the environment is unreachable
    or misconfigured, there is nothing to report at all, so this propagates
    rather than returning an empty, misleadingly "successful" scan. A failure
    reading one specific stem's manifest or its flagged version's
    `outlier_report.json` (malformed JSON, an unsupported schema version, a
    missing output key, or a storage/network error) is caught per-stem and
    recorded in `errors`; the scan continues to the next stem — a corrupt or
    incomplete record for one experiment must not hide every other experiment's
    result in a one-shot forensic sweep over a potentially large bucket.
    """
    hits: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    experiments_scanned = 0

    names = list_prefix(f"{_OUTPUT_ROOT}/")
    stems = [
        name[len(_OUTLIERS_PREFIX) :]
        for name in names
        if name.startswith(_OUTLIERS_PREFIX)
    ]

    for stem in stems:
        experiments_scanned += 1
        try:
            manifest = AnalysisDir(
                _OUTPUT_ROOT, f"{stem}.csv", OUTLIERS_TOOL_CLASS
            ).read_manifest()
        except Exception as exc:  # noqa: BLE001 - best-effort forensic sweep, see docstring
            errors.append({"stem": stem, "error": safe_error_text(exc)})
            continue

        if manifest is None or manifest.latest is None:
            # No manifest, or no current "latest" to evaluate — nothing to check.
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
        if latest_entry.tool != REMOVE_OUTLIERS_TOOL_NAME:
            # Not expected in a real outliers_<stem> manifest (only
            # remove_outliers ever writes to this tool class) — defensive, not
            # a hit, and must not crash if it somehow occurred.
            continue

        try:
            report_key = latest_entry.output_keys[OUTLIER_REPORT_NAME]
            report = read_json(report_key)
            trustworthy = fit_is_trustworthy(report.get("goodness_of_fit"))
            if trustworthy is not False:
                continue
            hits.append(
                {
                    "stem": stem,
                    "run_ref": latest_entry.id,
                    "based_on_version": latest_entry.based_on_version,
                    "created_at": latest_entry.created_at,
                    "fit_quality": report["goodness_of_fit"]["fit_quality"],
                    "method": report["method"],
                    "n_outliers": report["n_outliers"],
                    "n_input_samples": report["n_input_samples"],
                    "n_output_samples": report["n_output_samples"],
                }
            )
        except Exception as exc:  # noqa: BLE001 - best-effort forensic sweep, see docstring
            errors.append({"stem": stem, "error": safe_error_text(exc)})
            continue

    return {
        "hits": hits,
        "errors": errors,
        "experiments_scanned": experiments_scanned,
    }


def write_report(report: dict[str, Any]) -> str:
    """Persist `report` as a self-describing, timestamped JSON object.

    Adds `scanned_at` (ISO-8601 UTC), `storage_backend`, and `scope_note` to the
    payload itself so the report stays interpretable — including its own
    detection-scope caveat — if later moved, renamed, or copied elsewhere.
    Writes under the shared `_audit_reports/` prefix (bloom#585) with a distinct
    filename prefix so the two scripts' reports never collide; the key includes
    a short random suffix (not just a per-second timestamp) so two runs
    completing in the same wall-clock second can't silently overwrite one
    another.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "scanned_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "storage_backend": active_backend_name(),
        "scope_note": SCOPE_NOTE,
        **report,
    }
    suffix = uuid.uuid4().hex[:8]
    key = (
        f"{_REPORT_PREFIX}untrustworthy_outlier_fits_"
        f"{now.strftime('%Y%m%dT%H%M%SZ')}_{suffix}.json"
    )
    write_json(key, payload)
    return key


def run() -> int:
    """Scan, persist the report, print it, and return an exit code.

    Returns `1` only when the scan couldn't run at all (enumeration failed --
    nothing to report). Returns `0` whenever the scan completes, including when
    it reports hits and/or per-stem errors: those are the script's normal,
    successful output, not a script failure.
    """
    try:
        report = scan_for_untrustworthy_outlier_fits()
    except Exception as exc:  # noqa: BLE001 - top-level failure, reported then exits non-zero
        print(
            f"error: could not enumerate manifests: {safe_error_text(exc)}",
            file=sys.stderr,
        )
        return 1

    key = write_report(report)
    print(json.dumps(report, indent=2))
    print(f"scope: {SCOPE_NOTE}")
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
