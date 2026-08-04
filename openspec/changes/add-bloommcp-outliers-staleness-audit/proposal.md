## Why

`fix-bloommcp-remove-outliers-tool-class` (#420, PR #576, merged into `staging`) closed the
go-forward hazard: `remove_outliers` now persists under its own `tool_class="outliers"`, and
`version="latest"` gives that class fixed priority over `qc`, so a later plain `qc_clean` re-run
can no longer silently revert an *existing* trim once one exists. That change's own design.md
Risks section and a post-implementation 5-lens review both flagged two related gaps, deliberately
left out of #420 to keep its scope to the resolution-logic fix, and filed as
[#585](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/585):

1. **No retroactive visibility.** Before #420 shipped, `remove_outliers` persisted under the
   *shared* `qc` tool class. Any experiment where `qc_clean → remove_outliers → qc_clean`
   happened before #420 merged may already have a silently-reverted trim sitting in
   production/staging right now — a downstream `pca_analysis` (etc.) call could have been
   unknowingly analyzing un-trimmed data ever since, with zero error or warning. Nothing today
   scans for this.
2. **No ambient staleness signal.** #420 added `experiment_utils._log_if_trim_is_stale`: a
   non-blocking `logger.info` that fires *at read time* when a resolved `outliers` entry's
   `based_on_version` no longer matches the current `qc`-class latest (i.e. a `qc_clean` has run
   since the trim was made). That log line only fires when something actually reads the
   experiment — there is no way to ask "which of my experiments currently have a stale trim?"
   without reading each one.

## What Changes

- **Extract the staleness comparison in `experiment_utils._log_if_trim_is_stale` into a reusable,
  importable primitive**, `experiment_utils.trim_staleness(stem)` (`None` when no `outliers`-class
  version exists at all — nothing to assess; "stale" when an `outliers`-class version exists but
  the `qc`-class manifest has no baseline to compare it against at all; otherwise the existing
  `based_on_version`-vs-current-`qc`-latest comparison). `_log_if_trim_is_stale` becomes a thin
  logging wrapper over it — its two existing tests keep passing unmodified, and the values it logs
  are unchanged.
- **Surface `trim_is_stale` as an optional, advisory-only top-level field in
  `list_existing_analyses`'s JSON response**, computed via `trim_staleness`, present only when the
  check succeeds and an `outliers`-class version exists for the experiment (omitted — not `false`
  — both when the experiment has never been trimmed and when the check itself fails; a failure is
  recorded in the response's existing `errors` list instead). This lets an agent or scientist see
  staleness ambiently, at the point where they already check prior analyses, without triggering an
  actual `require_clean=True` read — but it is documented as advisory only, never a substitute for
  the hard-erroring resolution path that remains the authoritative safety mechanism.
- **Add a one-time, read-only audit script**, `bloommcp/scripts/audit_stale_outlier_trims.py`,
  that scans every `qc_<stem>/manifest.json` in the `bloommcp-data` bucket and reports each
  experiment where a `remove_outliers`-authored version exists in history but the manifest's
  *current* `latest` entry was authored by a different tool — an experiment whose trim was
  silently superseded under the pre-#420 shared-`qc` scheme. (A `remove_outliers`-authored entry
  that is superseded by a *later* `remove_outliers`-authored entry — a legitimate re-trim, per open
  issue #419 — is explicitly not a hit.) The scan logic is an importable, unit-testable function
  that never touches `write_manifest`/`upload_file`/any per-experiment manifest write; a thin
  `main()` prints the report and also persists it as a timestamped JSON object under a dedicated
  `bloommcp_output/_audit_reports/` prefix. Any manifest read failure (malformed JSON, schema
  mismatch, or otherwise) is recorded as a per-experiment error and does not abort the rest of the
  scan; a failure to enumerate manifests at all is a loud, non-zero-exit failure.

## Non-Goals

- **No change to `version="latest"` resolution behavior.** Decision 4's fixed-priority trade-off
  (`fix-bloommcp-remove-outliers-tool-class`) is accepted and not reopened here — this change is
  detection/reporting only, exactly as #585 scopes it.
- **No auto-remediation.** Neither the audit nor the staleness field re-runs `remove_outliers` or
  otherwise mutates a manifest; both are report-only, matching #585's explicit ask.
- **No periodic/scheduled job or alerting.** The audit is a manually-invoked, one-time script; the
  ongoing signal is pull-based (`list_existing_analyses`, called per-experiment), not push. A
  scheduled scan was one of #585's candidate follow-ups but is not scoped here — nothing today
  calls `list_existing_analyses` for every experiment on a cadence, and building that cadence is a
  separate, unscoped concern.
- **No change to `ResultStore`/`StoredRun`.** `trim_is_stale` is a new top-level field on
  `list_existing_analyses`'s response, computed by reading manifests directly through
  `AnalysisDir` (exactly as `_log_if_trim_is_stale` already does) — not a new field on `StoredRun`
  or a change to `ResultStore.list_runs`'s per-tool-class run listing. This is a narrow, disclosed
  exception to the "consumers depend only on ports" contract `test_persistence_import_guard.py`
  enforces for `list_existing_analyses.py` — see design.md Decision 2.
- **No retroactive fix.** The audit script only *reports* pre-#420 silent reverts; deciding what
  to do about any hit (re-running `remove_outliers` on that experiment, or accepting the loss) is
  a manual, human follow-up outside this change's scope.
- **No cross-referencing of other tool classes' provenance to name which downstream runs (a
  specific `pca_analysis`, `clustering`, etc.) consumed the un-trimmed data.** The audit reports
  the `qc`/`outliers` manifest pattern itself, not a full consumption trace — a heavier, unscoped
  lift left as an Open Question (design.md).
- **No handling of a backend-split manifest history (open issue #573).** Both new surfaces inherit
  `_resolve_versioned_cleaned`'s existing single-active-backend assumption; see design.md
  Non-Goals and Risks.
- **No bulk "which of my currently-known experiments have a stale trim right now" scan.**
  `trim_is_stale` only answers this per-experiment; a bulk ongoing scan was one of #585's own
  candidate follow-ups and is left open (design.md Open Questions).

## Impact

- **Affected capability:** new `bloommcp-outliers-staleness-audit` (a detection/reporting
  capability layered on the `bloommcp-experiment-read` resolution logic `fix-bloommcp-remove-outliers-tool-class`
  shipped; that capability's spec is not modified by this change).
- **Affected code:**
  `bloommcp/src/bloom_mcp/experiment_utils.py` (`trim_staleness` extracted;
  `_log_if_trim_is_stale` refactored to call it),
  `bloommcp/src/bloom_mcp/sections/core/list_existing_analyses.py` (`trim_is_stale` field),
  new `bloommcp/scripts/audit_stale_outlier_trims.py`,
  new `bloommcp/tests/scripts/test_audit_stale_outlier_trims.py`,
  `bloommcp/tests/test_storage_backend.py` (new `trim_staleness` unit tests alongside the existing
  staleness-log tests; fixes the missing-teardown gap in its own `_local_backend` fixture — see
  tasks.md 1.1), `bloommcp/tests/conftest.py` (a new shared, properly-torn-down
  `local_manifest_backend` fixture, promoted out of `test_storage_backend.py` rather than
  copy-pasted into new test files), new `bloommcp/tests/manifest_fixtures.py` (the manifest-building
  helper functions promoted alongside it — a separate module, not `conftest.py`, to avoid an actual
  `sys.modules` name collision with `bloommcp/tests/smoke/conftest.py` found while running the full
  suite), a new discovery test file for the `trim_is_stale` field,
  `bloommcp/tests/test_persistence_import_guard.py` (one-sentence docstring note disclosing the
  narrow, transitive `trim_staleness` exception for `list_existing_analyses.py`).
- **No call-site or behavior change** to `remove_outliers`, `qc_clean`, or any `require_clean=True`
  consumer — this change only adds detection/reporting surfaces.
- **Post-review addendum (design.md):** an independent PR review of the implemented change found
  and fixed several further gaps — a same-second `created_at` tie-break bug in the audit script; a
  `post_420_status` annotation per hit (via `trim_staleness`) so a historical hit isn't reported
  identically forever after it's actually been remediated; `trim_based_on_qc_version`/
  `trim_current_qc_version` alongside `trim_is_stale` in `list_existing_analyses`'s response, so the
  ordinary-staleness-vs-no-baseline distinction reaches the calling agent, not only a log line; a
  new `experiment_utils.safe_error_text` redaction/truncation helper used by both new error paths;
  a `scope_note` embedded in the persisted audit report payload itself; collision-resistant report
  keys; and a `REMOVE_OUTLIERS_TOOL_NAME` constant with a regression test guarding the one part of
  its own drift risk a test actually can catch.
- Refs: #585 (this issue, closes), #420 / PR #576 (`fix-bloommcp-remove-outliers-tool-class`,
  the change that flagged both gaps and filed #585 as their follow-up), PR #587 (this change).
