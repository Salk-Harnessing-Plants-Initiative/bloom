## ADDED Requirements

### Requirement: Experiment-scoped audit sweep for `audit_stale_outlier_trims.py`

`bloommcp/scripts/audit_stale_outlier_trims.py` SHALL accept an optional experiment filter. The filter is passed as an
`experiments` sequence of experiment identifiers to `scan_for_stale_outlier_trims`, or as a repeatable
`--experiment IDENTIFIER` CLI flag. When the filter is given:

- The scan SHALL resolve each requested experiment's `qc_<stem>/` prefix directly.
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

- **WHEN** `scan_for_stale_outlier_trims(experiments=["exp_a.csv"])` runs against a backend that holds several experiments
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
- **THEN** the scoped scan examines the same `qc_exp.v2/` prefix and reports the same entry

#### Scenario: A prefix-escaping or empty value is rejected before any storage access

- **WHEN** the filter contains a value such as `../x.csv`, `a/b.csv`, `a\b.csv`, `/abs.csv`, `""`
  or `".."`
- **THEN** `scan_for_stale_outlier_trims` raises `ValueError`, and `run(["--experiment", <value>])` prints an error to
  stderr and returns `2`, with no storage listing and no report write

#### Scenario: An empty filter sequence is rejected, not treated as a full sweep

- **WHEN** `scan_for_stale_outlier_trims(experiments=[])` is called
- **THEN** it raises `ValueError` rather than silently sweeping the whole bucket

#### Scenario: `run()` ignores the host process's command line

- **WHEN** `run()` is called with no arguments while `sys.argv` holds unrelated arguments, such as
  pytest's own
- **THEN** it performs a full sweep and returns `0`

### Requirement: Scoped `audit_stale_outlier_trims.py` reports disclose their scope

The persisted report payload SHALL include an `experiment_scope` field, next to `scanned_at`,
`storage_backend` and `scope_note`. The field SHALL be `{"mode": "all"}` for a full sweep. For a
scoped run it SHALL be `{"mode": "experiments", "experiments": [{"requested": ..., "stem": ...},
...]}`. Consequently, a scoped report with no hits cannot be mistaken for a clean full-bucket
sweep.

In a scoped run, the scan result SHALL include an `unevaluated` list. It contains an entry
`{"stem": ..., "reason": "no_manifest"}` for each requested experiment with no
`qc_<stem>/manifest.json`, and an entry `{"stem": ..., "reason": "no_latest"}` for each whose
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

- **WHEN** a scoped scan names one experiment that has no `qc_<stem>/manifest.json`, and another
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

### Requirement: One-time historical silent-revert audit

The system SHALL provide a read-only, one-time audit script,
`bloommcp/scripts/audit_stale_outlier_trims.py`, whose core scan is an importable function that
enumerates every `qc_<stem>` manifest in the configured storage backend (or, when an experiment filter is given, only the requested experiments' manifests; see the experiment-scoped audit sweep requirement) and reports each
experiment where a `remove_outliers`-authored version exists in that manifest's history but the
manifest's *current* `latest` entry was authored by a different tool — i.e., an experiment whose
trim was silently superseded by a later plain clean under the pre-#420 shared-`qc` scheme. A
`remove_outliers`-authored entry that is not `latest` SHALL NOT be reported as a hit when the
entry that *is* `latest` was itself also authored by `remove_outliers` (a legitimate,
still-current re-trim — see #419 — is not a silent revert); when more than one
`remove_outliers`-authored entry could be "the superseded one," the tie SHALL resolve to the
most recently *committed* entry even when two entries share an identical `created_at` (a real
possibility at that field's second granularity). Each hit SHALL be annotated with a
`post_420_status` reflecting whether a later, post-#420 `remove_outliers` run (against the
separate `outliers_<stem>` manifest this scan does not otherwise read) has since remediated it.
The script SHALL NOT mutate, upload to, or delete any object under any `qc_<stem>` or
`outliers_<stem>` prefix; its own report file (below) is the one object it writes, under a
separate, dedicated prefix.

#### Scenario: A pre-#420 silent revert is reported

- **WHEN** a `qc_<stem>/manifest.json` contains a `remove_outliers`-authored `VersionEntry`
  somewhere in its version history, and the manifest's current `latest` entry was authored by a
  different tool (e.g. `qc_clean`)
- **THEN** the scan reports one hit for that stem, naming the most recently-committed
  `remove_outliers` entry's id and `created_at` (the trim that was superseded), and the current
  `latest` entry's id, tool, and `created_at`

#### Scenario: A legitimate re-trim with no intervening plain clean is not reported

- **WHEN** a `qc_<stem>/manifest.json` contains more than one `remove_outliers`-authored
  `VersionEntry` (e.g. a scientist re-ran `remove_outliers` with a different method after a poor
  fit, per #419), and the manifest's current `latest` entry is itself one of those
  `remove_outliers`-authored entries
- **THEN** the scan does not report that experiment as a hit, regardless of how many
  non-latest `remove_outliers` entries exist in its history

#### Scenario: A manifest with no `remove_outliers` history is not reported

- **WHEN** a `qc_<stem>/manifest.json` contains only `qc_clean`-authored entries
- **THEN** the scan does not report that experiment as a hit

#### Scenario: The most recently-committed superseded trim is named when more than one exists

- **WHEN** a `qc_<stem>/manifest.json` contains more than one `remove_outliers`-authored
  `VersionEntry` in its history, and the manifest's current `latest` entry was authored by a
  different tool
- **THEN** the reported hit names the `remove_outliers` entry with the latest `created_at` among
  them, not merely the first one encountered

#### Scenario: A same-second tie still names the later-committed entry

- **WHEN** two `remove_outliers`-authored `VersionEntry`s in the same manifest's history share an
  identical `created_at` (second-granularity timestamps, e.g. a scripted backfill or a rapid
  re-trim)
- **THEN** the reported hit names the one committed later (by its position in the manifest's
  version history), not whichever one a naive max-by-timestamp comparison would keep on a tie

#### Scenario: A dangling `latest` pointer is reported as an error, not a crash

- **WHEN** a `qc_<stem>/manifest.json` is schema-valid but its `latest` field names a version `id`
  absent from its own `versions` list
- **THEN** that stem's inconsistency is recorded in the report's error list and the scan continues
  — it is not treated as a hit and does not raise

#### Scenario: A hit is annotated with its current remediation status

- **WHEN** a hit is reported for a stem
- **THEN** it includes `post_420_status`: `"not_remediated"` when no `outliers_<stem>` manifest
  exists at all, `"remediated_and_current"` when one exists and is not stale relative to the
  current `qc`-class latest, `"remediated_but_stale_again"` when one exists but a further
  `qc_clean` has since run, or `"unknown"` when computing this itself fails (never aborting the
  scan over an annotation)

#### Scenario: A `qc_<stem>` prefix with no manifest at all is skipped, not reported

- **WHEN** `list_prefix` enumerates a `qc_<stem>` prefix under which no `manifest.json` exists
  (e.g. a legacy un-versioned cleaned CSV with no manifest ever written, or an interrupted commit
  that uploaded outputs but never reached the manifest write)
- **THEN** the scan records that stem in neither `hits` nor `errors` — a missing manifest for an
  enumerated prefix is a normal, unremarkable state, not a failure

#### Scenario: An unreadable manifest does not abort the scan

- **WHEN** one `qc_<stem>/manifest.json` fails to parse or validate — whether malformed JSON, a
  schema-version mismatch, or a field-validation failure — while scanning multiple experiments
- **THEN** that experiment's stem and the error are recorded in the report's error list, and the
  scan continues to completion over the remaining experiments

#### Scenario: An empty bucket produces an empty, successful report

- **WHEN** a full sweep runs and no `qc_<stem>` manifests exist at all
- **THEN** it completes with zero hits and zero errors

#### Scenario: The scan never mutates an experiment's own manifests

- **WHEN** the scan runs, including over experiments it reports as hits or errors
- **THEN** no `qc_<stem>` or `outliers_<stem>` manifest is written, and no object under either
  prefix is uploaded or deleted

#### Scenario: A failure to enumerate manifests at all is a hard, loud failure

- **WHEN**, in a full sweep, the top-level `list_prefix` call used to discover `qc_<stem>` manifests itself fails
  (e.g. the storage backend is unreachable or misconfigured)
- **THEN** the script exits non-zero with a clear error message, rather than reporting an empty,
  misleadingly "successful" scan

#### Scenario: A completed scan always exits successfully, regardless of findings

- **WHEN** the scan completes — with any number of hits and/or per-stem errors (in a scoped run, with at least one requested experiment read without error)
- **THEN** the script exits `0`; a non-empty hit list or a partially-unreadable manifest are normal
  output, not a script failure

#### Scenario: The report is persisted and self-describing, not only printed

- **WHEN** the script runs to completion (in a scoped run, with at least one requested experiment read without error)
- **THEN** it writes the full report as JSON to a timestamped object under a dedicated
  `bloommcp_output/_audit_reports/` prefix (distinct from, and never overwriting, any
  `qc_<stem>`/`outliers_<stem>` manifest), in addition to printing it to stdout — the payload
  itself (not only the object's key/filename) includes a `scanned_at` UTC timestamp, the
  `storage_backend` that was scanned, and a `scope_note` describing the current-state-only
  detection scope, so the report remains interpretable — including its own caveat — if later moved,
  renamed, or copied elsewhere (e.g. pasted into a ticket with no memory of this script)

#### Scenario: Two reports never collide, even within the same second

- **WHEN** `write_report` is called twice in quick succession (plausibly within the same
  wall-clock second — two engineers running the audit, or a retry after what looked like a hang)
- **THEN** each call writes to a distinct key; neither silently overwrites the other
