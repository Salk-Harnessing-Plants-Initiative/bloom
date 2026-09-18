"""Read-only audit for #573 task 5.6: classify every catalog's backend sentinel.

Sweeps every `bloommcp_output/<tool_class>_<stem>/manifest.json` in the
configured storage backend and classifies its `storage_backend` sentinel:

- **matching** — sentinel equals the active backend: the #573 read guard
  serves these unchanged.
- **foreign** — sentinel names the *other* recognized backend: every read of
  this catalog FAILS CLOSED the moment the guard deploys. In containerized
  staging/prod there is no env-flip mitigation (the escape hatch is not passed
  through compose), so any hit here must be resolved before merging #782.
- **unstamped** — the sentinel is absent or empty (written before #572):
  these pass the guard silently and are its documented blind spot until each
  catalog's next commit re-stamps it. The count belongs in the PR body — it is
  the measure of how live the guard actually is on day one.
- **unrecognized** — a present sentinel that is not a string or names no
  recognized backend: the guard fails closed on these too (clamped display).

Run against each deployed environment with that environment's storage env
(e.g. prod's `SUPABASE_URL`/`BLOOM_AGENT_KEY`; or `BLOOM_STORAGE_BACKEND=local`
plus a root for an offline copy). Reads go through `read_json` directly —
deliberately *below* `read_manifest`, so foreign catalogs are classified
rather than raising and no escape hatch is needed; the audit itself writes
nothing.

Exit codes: 0 = no foreign/unrecognized sentinel (guard activation is a
verified non-event); 2 = at least one catalog the guard would refuse; 1 = the
sweep itself could not run (enumeration failed — nothing to report).
"""

from __future__ import annotations

import json
import sys
from typing import Any

from bloom_mcp.experiment_utils import safe_error_text
from bloom_mcp.storage_backend import VALID_BACKENDS, active_backend_name
from bloom_mcp.supabase_client import list_prefix, read_json

_OUTPUT_ROOT = "bloommcp_output"
_MANIFEST_BASENAME = "manifest.json"


def classify_sentinel(value: object, active: str) -> str:
    """One catalog's classification — mirrors `manifest.foreign_sentinel`'s
    rules exactly (absent/empty pass; strip+lower compare; non-str or
    out-of-VALID_BACKENDS is unrecognized), but keeps "matching" and
    "unstamped" distinct where the guard's predicate collapses both to
    "not foreign"."""
    if value is None:
        return "unstamped"
    if not isinstance(value, str):
        return "unrecognized"
    recorded = value.strip().lower()
    if not recorded:
        return "unstamped"
    if recorded not in VALID_BACKENDS:
        return "unrecognized"
    return "matching" if recorded == active else "foreign"


def scan_backend_sentinels() -> dict[str, Any]:
    """Classify every catalog under `bloommcp_output/`.

    Enumeration is unguarded (an unreachable environment must fail the sweep,
    not report an empty, misleadingly clean bucket — same policy as the other
    audit scripts). A failure reading one catalog's manifest is recorded in
    `errors` and the sweep continues.
    """
    active = active_backend_name()
    catalogs: dict[str, list[str]] = {
        "matching": [],
        "foreign": [],
        "unstamped": [],
        "unrecognized": [],
    }
    errors: list[dict[str, str]] = []
    scanned = 0

    for name in list_prefix(f"{_OUTPUT_ROOT}/"):
        prefix = f"{_OUTPUT_ROOT}/{name}"
        try:
            if _MANIFEST_BASENAME not in list_prefix(prefix):
                continue  # a prefix with no manifest is a normal legacy state
            raw = read_json(f"{prefix}/{_MANIFEST_BASENAME}")
        except Exception as exc:  # noqa: BLE001 - best-effort forensic sweep
            errors.append({"catalog": name, "error": safe_error_text(exc)})
            continue
        scanned += 1
        kind = classify_sentinel(raw.get("storage_backend"), active)
        catalogs[kind].append(name)

    return {
        "active_backend": active,
        "catalogs_scanned": scanned,
        "counts": {k: len(v) for k, v in catalogs.items()},
        "catalogs": catalogs,
        "errors": errors,
    }


def run() -> int:
    try:
        report = scan_backend_sentinels()
    except Exception as exc:  # noqa: BLE001 - top-level failure, then non-zero
        print(
            f"error: could not enumerate manifests: {safe_error_text(exc)}",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(report, indent=2))
    counts = report["counts"]
    refused = counts["foreign"] + counts["unrecognized"]
    print(
        f"{report['catalogs_scanned']} catalogs scanned under active backend "
        f"{report['active_backend']!r}: {counts['matching']} matching, "
        f"{counts['foreign']} foreign, {counts['unrecognized']} unrecognized "
        f"(the guard refuses reads of these {refused}), "
        f"{counts['unstamped']} unstamped (the guard's blind spot until each "
        f"is re-stamped by its next commit), {len(report['errors'])} errors"
    )
    return 2 if refused else 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
