# bloommcp-outliers-staleness-audit Specification

## Purpose
TBD - created by archiving change add-bloommcp-outliers-staleness-audit. Update Purpose after archive.
## Requirements
### Requirement: Shared trim-staleness primitive

The system SHALL provide a single function, `experiment_utils.trim_staleness(stem)`, that
determines whether an experiment's current `outliers`-class `latest` version is stale relative to
the current `qc`-class `latest` version, and every staleness-detection surface (the read-time log,
the `list_existing_analyses` field, below) SHALL compute staleness by calling it rather than
re-implementing the comparison. It SHALL propagate a manifest read failure to its caller rather
than swallowing it — each caller decides its own failure policy.

#### Scenario: No trim exists

- **WHEN** `trim_staleness(stem)` is called for an experiment with no `outliers`-class version
- **THEN** it returns `None` — there is nothing to assess

#### Scenario: Trim is current

- **WHEN** `trim_staleness(stem)` is called and the `outliers`-class latest entry's
  `based_on_version` matches the `qc`-class latest entry's label exactly
- **THEN** it reports "not stale"

#### Scenario: Trim is stale

- **WHEN** `trim_staleness(stem)` is called and a `qc_clean` has committed a new `qc`-class latest
  version since the `outliers`-class latest entry was made (its `based_on_version` no longer
  matches the current `qc`-class latest label)
- **THEN** it reports "stale"

#### Scenario: Trim exists with no `qc`-class baseline at all

- **WHEN** `trim_staleness(stem)` is called for an experiment with an `outliers`-class latest
  version but no `qc`-class manifest entry at all (no `latest` to compare against)
- **THEN** it reports "stale" — a trim with no live baseline to confirm it against is treated as a
  more concerning state than "current," not silently treated as "nothing to see" (a deliberate
  correction of the pre-existing, untested `_log_if_trim_is_stale` behavior for this corner, made
  while this logic is already being touched)

#### Scenario: Read-time log is unaffected in its two currently-tested cases

- **WHEN** `_resolve_versioned_cleaned` resolves `version="latest"` via the `outliers` class
- **THEN** it logs a non-blocking `logger.info`, naming the resolved trim's `based_on_version` and
  the current `qc`-class latest label, if and only if `trim_staleness` reports "stale" for that
  experiment — the existing staleness-log behavior and its two pre-existing tests (trim is
  current; trim is stale) are unaffected by this refactor

### Requirement: Ongoing staleness visibility in `list_existing_analyses`

`list_existing_analyses`'s JSON response SHALL include a top-level `trim_is_stale` boolean field
whenever `trim_staleness` successfully resolves a non-`None` result for the queried experiment, so
an agent or scientist can discover a stale trim without performing a `require_clean=True` read.
When present, the response SHALL also include `trim_based_on_qc_version` and
`trim_current_qc_version` (the latter `None` in the no-`qc`-baseline-at-all case) so a caller can
tell ordinary staleness apart from that more concerning corner without relying on a server-side log
line it cannot see. The `trim_is_stale` field SHALL be omitted (not `false`) both when the
experiment has never been trimmed and when computing it failed; on failure, the failure SHALL be
recorded in the response's existing `errors` list, using bounded/redacted exception text (see the
shared `experiment_utils.safe_error_text` helper), and any unrelated tool-class error already
collected SHALL still be present alongside it. The tool's documentation SHALL disclose that this
field is advisory only — its absence does not, by itself, mean the experiment was never trimmed —
and that responses (including `trim_is_stale`) are cached for up to 30 seconds with no
invalidation hook on a `qc_clean`/`remove_outliers` commit, so a check performed immediately after
such a commit may still return the pre-commit cached value.

#### Scenario: Untrimmed experiment sees no new field

- **WHEN** `list_existing_analyses(experiment_filename)` is called for an experiment with no
  `outliers`-class version
- **THEN** the response contains no `trim_is_stale` key and no new entry in `errors` — byte-for-byte
  the same shape this tool already returns today for that case

#### Scenario: Trimmed-and-current experiment reports not stale

- **WHEN** `list_existing_analyses(experiment_filename)` is called for an experiment whose
  `outliers`-class latest trim is based on the current `qc`-class latest
- **THEN** the response includes `"trim_is_stale": false`

#### Scenario: Trimmed-and-stale experiment reports stale

- **WHEN** `list_existing_analyses(experiment_filename)` is called for an experiment whose
  `outliers`-class latest trim's `based_on_version` no longer matches the current `qc`-class
  latest (or has no `qc`-class baseline at all)
- **THEN** the response includes `"trim_is_stale": true`

#### Scenario: A staleness-check failure does not fail the whole call, and is distinguishable from "never trimmed"

- **WHEN** computing `trim_staleness` for the queried experiment raises (e.g. a manifest schema
  error, or the storage backend is unreachable)
- **THEN** `list_existing_analyses` still returns its per-tool-class `analyses` results, appends a
  `"trim_staleness: ..."`-prefixed, bounded/redacted description of the failure to the response's
  `errors` list, and omits `trim_is_stale` — a caller that finds `trim_is_stale` absent SHALL be
  documented to check `errors` for a `trim_staleness` entry before concluding the experiment was
  never trimmed

#### Scenario: The staleness reason is distinguishable, not just the boolean

- **WHEN** `list_existing_analyses(experiment_filename)` reports `"trim_is_stale": true`
- **THEN** the response also includes `trim_current_qc_version` — a real `qc`-class version label
  when a `qc_clean` ran since the trim was made, or `None` when no `qc`-class baseline exists for
  this experiment at all — so a caller can tell ordinary staleness apart from that more concerning,
  corruption-adjacent corner (`trim_staleness`'s no-baseline case) without needing the server-side
  log line

#### Scenario: A staleness result and an unrelated tool-class error both survive together

- **WHEN** computing `trim_staleness` succeeds for the queried experiment, and, in the same call, a
  different tool class's `list_runs` lookup fails
- **THEN** the response includes both `trim_is_stale` (and its accompanying version fields) and the
  unrelated tool-class's error in `errors` — neither is dropped because the other succeeded or
  failed

### Requirement: One-time historical silent-revert audit

The system SHALL provide a read-only, one-time audit script,
`bloommcp/scripts/audit_stale_outlier_trims.py`, whose core scan is an importable function that
enumerates every `qc_<stem>` manifest in the configured storage backend and reports each
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

- **WHEN** the scan runs and no `qc_<stem>` manifests exist at all
- **THEN** it completes with zero hits and zero errors

#### Scenario: The scan never mutates an experiment's own manifests

- **WHEN** the scan runs, including over experiments it reports as hits or errors
- **THEN** no `qc_<stem>` or `outliers_<stem>` manifest is written, and no object under either
  prefix is uploaded or deleted

#### Scenario: A failure to enumerate manifests at all is a hard, loud failure

- **WHEN** the top-level `list_prefix` call used to discover `qc_<stem>` manifests itself fails
  (e.g. the storage backend is unreachable or misconfigured)
- **THEN** the script exits non-zero with a clear error message, rather than reporting an empty,
  misleadingly "successful" scan

#### Scenario: A completed scan always exits successfully, regardless of findings

- **WHEN** the scan completes — with any number of hits and/or per-stem errors
- **THEN** the script exits `0`; a non-empty hit list or a partially-unreadable manifest are normal
  output, not a script failure

#### Scenario: The report is persisted and self-describing, not only printed

- **WHEN** the script runs to completion
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

