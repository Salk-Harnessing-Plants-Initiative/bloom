## ADDED Requirements

### Requirement: Experiment-scoped audit sweep for `audit_untrustworthy_outlier_fits.py`

`bloommcp/scripts/audit_untrustworthy_outlier_fits.py` SHALL accept an optional experiment filter. The filter is passed as an
`experiments` sequence of experiment identifiers to `scan_for_untrustworthy_outlier_fits`, or as a repeatable
`--experiment IDENTIFIER` CLI flag. When the filter is given:

- The scan SHALL resolve each requested experiment's `outliers_<stem>/` prefix directly.
- It SHALL NOT list the shared `bloommcp_output/` root.
- It SHALL derive each stem exactly as `AnalysisDir` does, with `Path(identifier).stem`. To target
  a stem copied from an earlier report, pass `<stem>.csv`, which is how the full sweep itself
  rebuilds its `AnalysisDir`.
- Before any storage call, it SHALL reject with `ValueError` any requested value that:

  - is not a string;
  - is empty or whitespace-only;
  - contains `/`, `\` or a NUL character;
  - has a stem that is empty, `.` or `..`.

  The check applies to the raw value, not to the derived stem.

- It SHALL reject an empty `experiments` sequence with `ValueError`. `None` means a full sweep.
- It SHALL collapse values that resolve to the same stem into one entry, keeping the first-seen
  order and the first-seen value.
- For each requested experiment, it SHALL report the same hit or error entry that a full sweep
  reports for it.

`run(argv=())` SHALL parse only the arguments it is given, never `sys.argv`. `main()` SHALL pass
`sys.argv[1:]`.

#### Scenario: A scoped scan never enumerates the shared root

- **WHEN** `scan_for_untrustworthy_outlier_fits(experiments=["exp_a.csv"])` runs against a backend that holds several experiments
- **THEN** the backend receives no `list_prefix` call for `bloommcp_output/` (or the bucket root),
  every listing it does receive is under an `exp_a` analysis prefix, and `experiments_scanned == 1`

#### Scenario: A scoped scan reports the same entry as the full sweep for that experiment

- **WHEN** an experiment is reported as a hit, or as an error, by the full sweep, and other
  experiments are hits too
- **THEN** a scan scoped to that experiment reports an identical hit (or error) dict for it, and no
  hit or error for any experiment outside the scope

#### Scenario: Duplicate requests collapse to one experiment

- **WHEN** the filter is `["exp_a.csv", "exp_a"]`
- **THEN** `experiments_scanned == 1`, and the recorded experiment scope has a single entry whose
  requested value is `"exp_a.csv"`

#### Scenario: A stem copied from a report round-trips via `<stem>.csv`

- **WHEN** a full sweep reports stem `exp.v2`, and a scoped scan is run with `exp.v2.csv`
- **THEN** the scoped scan examines the same `outliers_exp.v2/` prefix and reports the same entry

#### Scenario: A prefix-escaping or empty value is rejected before any storage access

- **WHEN** the filter contains a value such as `../x.csv`, `a/b.csv`, `a\b.csv`, `/abs.csv`, `""`
  or `".."`
- **THEN** `scan_for_untrustworthy_outlier_fits` raises `ValueError`, and `run(["--experiment", <value>])` prints an error to
  stderr and returns `2`, with no storage listing and no report write

#### Scenario: An empty filter sequence is rejected, not treated as a full sweep

- **WHEN** `scan_for_untrustworthy_outlier_fits(experiments=[])` is called
- **THEN** it raises `ValueError` rather than silently sweeping the whole bucket

#### Scenario: `run()` ignores the host process's command line

- **WHEN** `run()` is called with no arguments while `sys.argv` holds unrelated arguments, such as
  pytest's own
- **THEN** it performs a full sweep and returns `0`

### Requirement: Scoped `audit_untrustworthy_outlier_fits.py` reports disclose their scope

The persisted report payload SHALL include an `experiment_scope` field, next to `scanned_at`,
`storage_backend` and `scope_note`. The field SHALL be `{"mode": "all"}` for a full sweep. For a
scoped run it SHALL be `{"mode": "experiments", "experiments": [{"requested": ..., "stem": ...},
...]}`. Consequently, a scoped report with no hits cannot be mistaken for a clean full-bucket
sweep.

In a scoped run, the scan result SHALL include an `unevaluated` list. It contains an entry
`{"stem": ..., "reason": "no_manifest"}` for each requested experiment with no
`outliers_<stem>/manifest.json`, and an entry `{"stem": ..., "reason": "no_latest"}` for each whose
manifest has no `latest` pointer. Those experiments SHALL NOT appear in `hits` or `errors`. They
SHALL still count toward `experiments_scanned`.

A full sweep's scan result SHALL be unchanged: it carries no `unevaluated` key.

A scoped run's printed summary line SHALL begin with `scoped to N experiment(s): `. A full
sweep's summary line SHALL NOT contain `scoped to`.

When a scoped run's every requested experiment ends in `errors`, `run()` SHALL NOT persist a
report. It SHALL print the result and an error to stderr, and return `1`. This matches a full
sweep's enumeration-failure contract, because an unreachable backend surfaces this way in scoped
mode.

#### Scenario: The persisted report records its experiment scope

- **WHEN** `run(["--experiment", "exp_a.csv"])` completes
- **THEN** the persisted payload's `experiment_scope` is
  `{"mode": "experiments", "experiments": [{"requested": "exp_a.csv", "stem": "exp_a"}]}`

#### Scenario: A full sweep's report records mode `all` and its result shape is unchanged

- **WHEN** `run()` completes with no `--experiment` flag
- **THEN** the persisted payload's `experiment_scope` is `{"mode": "all"}`, the scan result has no
  `unevaluated` key, and the summary line does not contain `scoped to`

#### Scenario: A requested experiment with nothing to evaluate is disclosed, not silently clean

- **WHEN** a scoped scan names one experiment that has no `outliers_<stem>/manifest.json`, and another
  whose manifest has no `latest` pointer
- **THEN** `unevaluated` lists them with reasons `no_manifest` and `no_latest` respectively,
  neither appears in `hits` or `errors`, and both count toward `experiments_scanned`

#### Scenario: A scoped run in which every requested experiment fails is a loud failure

- **WHEN** a scoped run's every requested experiment ends in `errors`, for example because the
  storage backend is unreachable
- **THEN** `run()` returns `1`, prints the error to stderr, and persists no report

#### Scenario: A scoped scan never mutates an experiment's own manifests

- **WHEN** a scoped scan runs over experiments that it reports as hits or errors
- **THEN** no manifest is written, and no object under any `qc_<stem>` or `outliers_<stem>` prefix
  is uploaded or deleted

## MODIFIED Requirements

### Requirement: One-time audit of already-persisted untrustworthy-fit trims

The system SHALL provide a read-only script, `bloommcp/scripts/audit_untrustworthy_outlier_fits.py`,
that scans every `outliers_<stem>` manifest in the configured storage backend (or, when an experiment filter is given, only the requested experiments' manifests; see the experiment-scoped audit sweep requirement) and reports each
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

- **WHEN**, in a full sweep, the storage backend cannot be enumerated at all (e.g. unreachable/misconfigured)
- **THEN** the script exits non-zero and writes no report — there is nothing to report if the
  bucket itself could not be listed

#### Scenario: A successful scan persists a durable, self-describing report

- **WHEN** the scan completes (with or without hits/errors; in a scoped run, with at least one requested experiment read without error)
- **THEN** the script persists the report as a timestamped JSON object under
  `bloommcp_output/_audit_reports/`, including `scanned_at`, `storage_backend`, and a `scope_note`
  describing the scan's own scope limits, in the payload itself — not only in the script's
  docstring — so the report remains self-describing if read or copied elsewhere later

#### Scenario: Two reports completed within the same second never collide

- **WHEN** two runs of the script complete their report-writing step within the same wall-clock
  second (e.g. two engineers, or a retry)
- **THEN** each produces a distinct report object key — neither silently overwrites the other
