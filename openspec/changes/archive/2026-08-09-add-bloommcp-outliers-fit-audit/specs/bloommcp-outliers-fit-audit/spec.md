## ADDED Requirements

### Requirement: Shared fit-trustworthiness primitive

The system SHALL provide `experiment_utils.UNTRUSTWORTHY_FIT_QUALITIES`,
`experiment_utils.fit_is_trustworthy(goodness_of_fit)`, and `experiment_utils.OUTLIER_REPORT_NAME`
as the single source of truth for classifying a mahalanobis fit as untrustworthy and for naming
the persisted fit-report file. `remove_outliers`'s own live pre-commit gate SHALL compute this
classification by calling `fit_is_trustworthy`, not by re-implementing the comparison, so the live
gate and this audit can never silently disagree on what counts as untrustworthy.

#### Scenario: remove_outliers's gate and the audit agree by construction

- **WHEN** `remove_outliers`'s pre-commit gate and the audit script each evaluate the same
  `goodness_of_fit` dict
- **THEN** both call `experiment_utils.fit_is_trustworthy` and receive the identical result — there
  is exactly one implementation of this classification in the codebase

### Requirement: One-time audit of already-persisted untrustworthy-fit trims

The system SHALL provide a read-only script, `bloommcp/scripts/audit_untrustworthy_outlier_fits.py`,
that scans every `outliers_<stem>` manifest in the configured storage backend and reports each
experiment whose current `latest` `remove_outliers`-authored version's persisted
`outlier_report.json` records an untrustworthy `goodness_of_fit.fit_quality`
(`fit_is_trustworthy` is `False`) — a trim that predates the `remove_outliers` fit-trustworthiness
gate and would be rejected under today's code. The scan SHALL NOT mutate any experiment manifest,
re-run `remove_outliers`, or otherwise alter the flagged run.

#### Scenario: An untrustworthy-fit trim that predates the gate is reported

- **WHEN** the audit scans an `outliers_<stem>` manifest whose current `latest` entry is
  `remove_outliers`-authored and whose persisted `outlier_report.json` records
  `goodness_of_fit.fit_quality` of `"poor"`, `"very_poor"`, or `"unknown"`
- **THEN** the report includes a hit naming the stem, the flagged version's id, its
  `based_on_version`, `created_at`, the recorded `fit_quality`, the trim's `method`, and the
  report's `n_outliers` / `n_input_samples` / `n_output_samples`

#### Scenario: A trustworthy-fit or isolation_forest trim is not a hit

- **WHEN** the audit scans a manifest whose current `latest` entry's `outlier_report.json` records
  an acceptable-or-better `fit_quality`, or has `goodness_of_fit` absent entirely (an
  `isolation_forest` trim)
- **THEN** that experiment is not reported as a hit

#### Scenario: A latest entry not authored by remove_outliers is not a hit

- **WHEN** the audit scans an `outliers_<stem>` manifest whose current `latest` entry's `tool` is
  not `"remove_outliers"` (not expected in a real `outliers_<stem>` manifest, since only
  `remove_outliers` writes to that tool class, but defensive rather than assumed)
- **THEN** that experiment is not reported as a hit and the scan does not crash

#### Scenario: A pre-#420 legacy qc-manifest entry is out of scope, not a hit

- **WHEN** an experiment's `qc_<stem>` manifest's current `latest` entry is
  `remove_outliers`-authored with an untrustworthy fit, but that experiment has no
  `outliers_<stem>` manifest at all (a pre-#420 trim never superseded by any subsequent
  `qc_clean` or post-#420 `remove_outliers` run)
- **THEN** the audit does not report it — this scan is deliberately scoped to `outliers_<stem>`
  manifests only (see design.md Decision 2); this narrower edge case is a disclosed, tracked
  scope limit, not a silent gap

#### Scenario: A manifest read or report read failure does not abort the scan

- **WHEN** one experiment's `outliers_<stem>/manifest.json` is malformed, or its flagged version's
  `outlier_report.json` is missing or unreadable
- **THEN** that failure is recorded in the report's `errors` list (naming the stem) and the scan
  continues to the next experiment — a single corrupt or incomplete record does not hide every
  other experiment's result

#### Scenario: Enumeration failure aborts with a non-zero exit

- **WHEN** the storage backend cannot be enumerated at all (e.g. unreachable/misconfigured)
- **THEN** the script exits non-zero and writes no report — there is nothing to report if the
  bucket itself could not be listed

#### Scenario: A successful scan persists a durable, self-describing report

- **WHEN** the scan completes (with or without hits/errors)
- **THEN** the script persists the report as a timestamped JSON object under
  `bloommcp_output/_audit_reports/`, including `scanned_at`, `storage_backend`, and a `scope_note`
  describing the scan's own scope limits, in the payload itself — not only in the script's
  docstring — so the report remains self-describing if read or copied elsewhere later

#### Scenario: Two reports completed within the same second never collide

- **WHEN** two runs of the script complete their report-writing step within the same wall-clock
  second (e.g. two engineers, or a retry)
- **THEN** each produces a distinct report object key — neither silently overwrites the other
