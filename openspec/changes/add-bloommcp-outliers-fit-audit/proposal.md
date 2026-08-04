## Why

`fix-bloommcp-remove-outliers-fit-gate` (#419, PR #592, merged into `staging`) closed the
go-forward hazard: `remove_outliers` now raises a structured `assumption_violated` error
**before** persisting, instead of silently committing a trim whose mahalanobis chi-squared fit
is untrustworthy (`fit_is_trustworthy is False` — poor/very_poor/unknown `goodness_of_fit`). That
fix's own design.md disclosed, and a PR review confirmed, that it is **not retroactive**: any
`outliers`-class run already persisted *before* the gate shipped, from a mahalanobis trim whose
fit was untrustworthy at commit time, remains exactly as it is — silently canonical, with nothing
distinguishing it from a trustworthy-fit trim. Filed as
[#593](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/593), explicitly the
same shape as [#585](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/585) (the
analogous retroactive-audit follow-up `fix-bloommcp-remove-outliers-tool-class` / #420 filed).

## What Changes

- **Extract the fit-trustworthiness check in `remove_outliers.py` into a reusable, importable
  primitive in `experiment_utils.py`**: `UNTRUSTWORTHY_FIT_QUALITIES` (the
  `{"poor", "very_poor", "unknown"}` frozenset, promoted from `remove_outliers._UNTRUSTWORTHY_FIT`)
  and `fit_is_trustworthy(goodness_of_fit)` (promoted from `remove_outliers._fit_is_trustworthy`,
  identical behavior). `remove_outliers.py` imports both instead of defining its own private
  copies — the same "extract to `experiment_utils` so the audit script and the tool share one
  source of truth" pattern `#585` already established for `trim_staleness`. Also promotes the
  `outlier_report.json` filename (`remove_outliers._REPORT_NAME`) to a public
  `OUTLIER_REPORT_NAME` constant for the same reason: the audit script needs to name that file
  too, and a second private literal would be exactly the drift risk a recent PR review flagged
  for this tool's other duplicated constants.
- **Add a one-time, read-only audit script**, `bloommcp/scripts/audit_untrustworthy_outlier_fits.py`,
  mirroring `audit_stale_outlier_trims.py`'s shape: scans every `outliers_<stem>/manifest.json` in
  the configured storage backend, and for each manifest whose current `latest` entry is
  `remove_outliers`-authored, reads that entry's persisted `outlier_report.json`
  (via `output_keys`) and reports a hit when its `goodness_of_fit.fit_quality` is untrustworthy —
  i.e. a run that would be gated under today's code but predates it. Read-only except for its own
  persisted report (reusing the `bloommcp_output/_audit_reports/` prefix `#585` established, with
  a distinct filename prefix so the two scripts' reports never collide).
- **New test file** `bloommcp/tests/scripts/test_audit_untrustworthy_outlier_fits.py`, following
  `test_audit_stale_outlier_trims.py`'s pattern (real on-disk manifests via the existing
  `local_manifest_backend` fixture, script loaded by path since `bloommcp/scripts/` isn't a
  package). **New fixture helper** in `manifest_fixtures.py` (additive — existing helpers there
  are untouched): the current helpers only write `_cleaned.csv` and never populate
  `VersionEntry.output_keys`, so a new helper is needed to write both `_cleaned.csv` and a
  `outlier_report.json` with a controllable `goodness_of_fit`, matching what a real
  `remove_outliers` commit actually produces.

## Non-Goals

- **No change to `remove_outliers`'s runtime behavior.** The #419 gate's resolution logic is not
  reopened here; this change is detection/reporting only, exactly as #593 (and #585 before it)
  scopes it.
- **No auto-remediation.** The audit does not re-run `remove_outliers`, delete/supersede the
  flagged run, or mutate any manifest — report-only, matching #585's precedent and #593's own ask.
- **Does not additionally scan legacy `qc_<stem>` manifests for a never-superseded, pre-#420
  `remove_outliers` entry that still happens to be that manifest's `latest`** (a `qc_clean` →
  `remove_outliers` sequence, before #420 shipped, with no `qc_clean` *or* post-#420
  `remove_outliers` re-run since — so no `outliers_<stem>` manifest was ever created for it at
  all). This is a real, narrower edge case a reviewer should be aware of, not a silent gap — see
  design.md's Decisions/Open Questions for why it's scoped out rather than folded in here.
- **No periodic/scheduled job.** A manually-invoked, one-time script, same as `#585`.
- **No cross-referencing of downstream consumers** (which `pca_analysis`/`clustering`/etc. runs
  actually consumed a flagged trim) — the audit reports the `outliers` manifest + report pattern
  itself, not a full consumption trace, same scope limit `#585` already accepted for its own audit.

## Impact

- **Affected capability:** new `bloommcp-outliers-fit-audit` (a detection/reporting capability
  layered on the `#419` gate; that capability's own spec is not modified by this change).
- **Affected code:**
  - `bloommcp/src/bloom_mcp/experiment_utils.py` (`UNTRUSTWORTHY_FIT_QUALITIES`,
    `fit_is_trustworthy`, `OUTLIER_REPORT_NAME` extracted/promoted)
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/remove_outliers.py` (imports the
    promoted primitives instead of defining private copies; no behavior change)
  - new `bloommcp/scripts/audit_untrustworthy_outlier_fits.py`
  - new `bloommcp/tests/scripts/test_audit_untrustworthy_outlier_fits.py`
  - `bloommcp/tests/manifest_fixtures.py` (one new, additive helper function)
- **No call-site or behavior change** to `remove_outliers` or any `require_clean=True` consumer.
- **PR target:** `staging` (this repo's standard integration branch — matching `#419`/`#585`'s
  own PRs). See design.md's Migration Plan for the recommended commit split.
- Refs: #593 (this issue, closes), #419 / PR #592 (the change that disclosed this gap and filed
  #593 as its follow-up), #585 / PR #587 (the analogous precedent this change's shape follows).
