## MODIFIED Requirements

### Requirement: One scan's failure is isolated, not fatal to the batch

The command SHALL stage every scan independently: a scan that fails (not found, zero frames,
invalid frame_numbers, metadata-resolution failure, a partial frame-download failure, or lock
contention with another live invocation) SHALL be recorded as `failed` with a per-scan error
message, and SHALL NOT prevent the remaining scans in the batch from being staged. The command
SHALL exit `3` if any scan in the batch failed — distinct from Click's own reserved exit codes
`1` (an uncaught exception or `ClickException`, e.g. a manifest-lock/write failure) and `2`
(`UsageError`) — and SHALL exit `0` if every scan succeeded, was skipped, or the input was empty.

#### Scenario: One bad scan among several does not abort the batch

- **WHEN** a batch of 3 scan_ids includes one scan with zero `cyl_images` rows
- **THEN** the other 2 scans are staged successfully (frames + sidecar present,
  `sleap_roots_predict.discover_scans` accepts both), the bad scan is reported `failed` by name
  with its reason, and the command exits `3`

#### Scenario: Empty scan_ids input is a no-op, not an error

- **WHEN** `--scan-ids-file`'s content is an empty JSON array (`[]`)
- **THEN** the command creates no output directories, reports zero scans, and exits `0`

#### Scenario: A usage error or manifest-lock failure never exits 3

- **WHEN** the command fails before or independently of any per-scan result — e.g.
  `--scan-ids-file`/`--scan-ids` are both given or both omitted (a `click.UsageError`, exit `2`),
  `--scan-ids-file`'s content isn't a valid JSON array of integers (a `click.ClickException`,
  exit `1`), or the `out_dir/.locks/manifest.lock` write-lock is contended (also a
  `click.ClickException`, exit `1`) — regardless of whether any scan in the batch would otherwise
  have succeeded
- **THEN** the command exits `2` or `1` respectively, never `3` — the exit code `3` is reserved
  exclusively for "the batch ran to completion and at least one scan's `ScanResult` was `failed`,"
  not for a failure that prevented per-scan staging from running at all

### Requirement: A RunManifest recording every usable scan_key is written after each invocation

The `batch-download-for-predict` command SHALL write a `sleap_roots_contracts.RunManifest` to
`out_dir / RUN_MANIFEST_FILENAME` — the filename constant imported from `sleap_roots_contracts`
(currently `"run_manifest.json"`), never a bloomctl-local literal, so that a downstream consumer
reading via the same constant finds the file this command writes. Its `scan_keys` SHALL be exactly
the scan_keys whose result this invocation was `ok` or `skipped` (excludes a scan that `failed`
this run). Its `pipeline_run_id` SHALL be the value of the `ARGO_WORKFLOW_NAME` environment
variable when set, or else a freshly generated `local-<8 hex chars>` placeholder distinct per
invocation. If `out_dir` already has a `RunManifest` from a prior invocation, the write SHALL merge
rather than overwrite: the resulting `scan_keys` SHALL be the union of the existing manifest's
scan_keys and this invocation's, and the resulting `pipeline_run_id` SHALL be this invocation's
(the most recent write wins), and `scan_keys` SHALL NOT contain duplicate entries when the two
sets overlap. If the existing file at `out_dir / RUN_MANIFEST_FILENAME` does not parse as valid
JSON matching `RunManifest`'s shape, the command SHALL fail with an actionable error rather than
silently treating it as absent. If this invocation's merged `scan_keys` would be empty (every scan
failed, and no pre-existing manifest to merge with), the command SHALL skip the manifest write
entirely rather than raise an unhandled error from constructing a `RunManifest` with empty
`scan_keys`.

#### Scenario: Manifest lists exactly the scans usable after a successful batch

- **WHEN** `bloomctl cyl batch-download-for-predict /tmp/stage --scan-ids 1,2,3` runs and all
  three scans stage successfully
- **THEN** `/tmp/stage/<RUN_MANIFEST_FILENAME>` exists and its `scan_keys` are exactly
  `["scan_1", "scan_2", "scan_3"]`

#### Scenario: A scan that failed this run is excluded from the manifest

- **WHEN** a batch of 3 scan_ids includes one scan with zero `cyl_images` rows (fails)
- **THEN** the written `RunManifest`'s `scan_keys` include only the two scans that succeeded, not
  the failed one

#### Scenario: A skipped (already-staged) scan is included in the manifest

- **WHEN** one scan_id in the batch was already staged from a prior invocation
  (`scan_is_already_staged` returns True, so this run reports it `skipped`)
- **THEN** that scan's `scan_key` is included in the written `RunManifest`'s `scan_keys`

#### Scenario: pipeline_run_id is sourced from ARGO_WORKFLOW_NAME when set

- **WHEN** the `ARGO_WORKFLOW_NAME` environment variable is set to `"wf-abc123"` when the command
  runs
- **THEN** the written `RunManifest`'s `pipeline_run_id` equals `"wf-abc123"`

#### Scenario: pipeline_run_id falls back to a generated placeholder outside Argo

- **WHEN** `ARGO_WORKFLOW_NAME` is not set in the environment
- **THEN** the command does not fail, and the written `RunManifest`'s `pipeline_run_id` matches
  `local-[0-9a-f]{8}`

#### Scenario: Two invocations without ARGO_WORKFLOW_NAME get distinguishable placeholders

- **WHEN** the command is run twice in a row, both times with `ARGO_WORKFLOW_NAME` unset
- **THEN** the two resulting `pipeline_run_id` values differ

#### Scenario: A second invocation with a disjoint scan set merges into the existing manifest

- **WHEN** `out_dir` already has a `RunManifest` with `scan_keys: ["scan_1", "scan_2"]` (from a
  prior invocation), and a new invocation stages `scan_id=3` successfully
- **THEN** the rewritten `RunManifest`'s `scan_keys` are `["scan_1", "scan_2", "scan_3"]` (the
  union, not just `["scan_3"]`), and `pipeline_run_id` equals this new invocation's value

#### Scenario: A repeated or overlapping scan_id does not create a duplicate manifest entry

- **GIVEN** `out_dir` already has a `RunManifest` with `scan_keys: ["scan_1", "scan_2"]`
- **WHEN** a new invocation stages `scan_ids=[2, 3]` and both succeed
- **THEN** the rewritten `RunManifest`'s `scan_keys` are exactly `["scan_1", "scan_2", "scan_3"]`
  — `scan_2` appears once, not twice

#### Scenario: A corrupt existing manifest fails loud instead of being silently discarded

- **GIVEN** `out_dir / RUN_MANIFEST_FILENAME` exists but is not valid JSON (or doesn't parse to
  a `RunManifest` shape)
- **WHEN** a new invocation finishes staging and attempts to write the manifest
- **THEN** the command exits non-zero with an actionable error, and the corrupt file is not
  silently overwritten with only this invocation's `scan_keys`

#### Scenario: An all-failed batch with no pre-existing manifest skips the write, not a crash

- **GIVEN** `out_dir` has no pre-existing manifest file
- **WHEN** every scan in the batch fails this run
- **THEN** the command exits `3` (the existing all-failed behavior), no manifest file is created,
  and no unhandled error is raised from constructing a `RunManifest` with empty `scan_keys`
