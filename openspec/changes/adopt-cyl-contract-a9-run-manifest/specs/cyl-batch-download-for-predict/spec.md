## MODIFIED Requirements

### Requirement: A RunManifest recording every usable scan_key is written after each invocation

The `batch-download-for-predict` command SHALL write a `sleap_roots_contracts.RunManifest` to
`out_dir / run_manifest_name_for_writing(pipeline_run_id_from_env())`, both functions imported from
`sleap_roots_contracts` (>= 0.1.0a9) and never a bloomctl-local literal or environment read. That
name is `run_manifest.<id>.json`, where `<id>` is the whitespace-stripped `ARGO_WORKFLOW_NAME`,
when that variable is set to a non-blank value, and `RUN_MANIFEST_FILENAME` (`"run_manifest.json"`)
when it is unset or blank. A downstream reader resolving its manifest through `load_run_manifest` with the same run identity therefore finds
the file this command writes.

The manifest's `scan_keys` SHALL be exactly the scan_keys whose result this invocation was `ok` or
`skipped`, so a scan that `failed` this run is excluded. Its `pipeline_run_id` SHALL be the value
`pipeline_run_id_from_env()` returned (whitespace-stripped). When that value is `None`, it SHALL be
a freshly generated `local-<8 hex chars>` placeholder, distinct per invocation. The placeholder
SHALL never be used to name the file.

The write SHALL replace the target file atomically (a temporary file in `out_dir` renamed over
it), so a reader never observes a partial manifest, whether or not a run identity exists. The
command SHALL NOT read, parse or merge any existing manifest, including a legacy `run_manifest.json` beside a
per-run file, and SHALL NOT write the legacy name when a run identity exists.

For a non-empty scan_ids input, the command SHALL resolve the manifest file name before
authenticating or staging any scan. If `run_manifest_name_for_writing` rejects the run identity (a
`ValueError` for an identifier the contract does not accept), the command SHALL fail with an
actionable `ClickException` (exit `1`) before any authentication, download or per-scan lock. An
empty scan_ids input writes no manifest, so it resolves no name and keeps its exit `0`.

If this invocation's `scan_keys` would be empty (every scan failed), the command SHALL skip the
manifest write rather than raise from constructing a `RunManifest` with empty `scan_keys`. It SHALL
leave any existing file of the same name untouched.

#### Scenario: Manifest lists exactly the scans usable after a successful batch

- **WHEN** `bloomctl cyl batch-download-for-predict /tmp/stage --scan-ids 1,2,3` runs with
  `ARGO_WORKFLOW_NAME` set to `"wf-abc123"` and all three scans stage successfully
- **THEN** `/tmp/stage/run_manifest.wf-abc123.json` exists and its `scan_keys` are exactly
  `["scan_1", "scan_2", "scan_3"]`
- **AND** no `/tmp/stage/run_manifest.json` is created

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

#### Scenario: A whitespace-padded ARGO_WORKFLOW_NAME names and stamps the same stripped id

- **WHEN** `ARGO_WORKFLOW_NAME` is set to `" wf-abc123\n"`
- **THEN** the file written is `run_manifest.wf-abc123.json` and its `pipeline_run_id` is exactly
  `"wf-abc123"`, so a reader's identity cross-check against `pipeline_run_id_from_env()` passes

#### Scenario: pipeline_run_id falls back to a generated placeholder outside Argo

- **WHEN** `ARGO_WORKFLOW_NAME` is not set in the environment
- **THEN** the command does not fail, the file written is `out_dir / "run_manifest.json"`, and its
  `pipeline_run_id` matches `local-[0-9a-f]{8}`

#### Scenario: A blank ARGO_WORKFLOW_NAME is treated as no run identity

- **WHEN** `ARGO_WORKFLOW_NAME` is set to `"   "`
- **THEN** the file written is `out_dir / "run_manifest.json"` and its `pipeline_run_id` matches
  `local-[0-9a-f]{8}`

#### Scenario: Two invocations without ARGO_WORKFLOW_NAME get distinguishable placeholders

- **WHEN** the command is run twice in a row, both times with `ARGO_WORKFLOW_NAME` unset
- **THEN** the two resulting `pipeline_run_id` values differ

#### Scenario: A retry of the same run records only its own usable keys

- **GIVEN** `ARGO_WORKFLOW_NAME` is `"wf-abc123"` and `out_dir / "run_manifest.wf-abc123.json"`
  already holds `scan_keys: ["scan_1", "scan_2"]` from an earlier attempt of the same workflow
- **WHEN** a retry stages `scan_ids=[2, 3]`, with `scan_2` `skipped` and `scan_3` `ok`
- **THEN** the rewritten manifest's `scan_keys` are exactly `["scan_2", "scan_3"]`, so `scan_1` is
  not carried forward

#### Scenario: A repeated scan_id does not create a duplicate manifest entry

- **WHEN** the command is run with `--scan-ids 2,2,3` and every scan stages or is skipped
- **THEN** the written manifest's `scan_keys` are exactly `["scan_2", "scan_3"]`

#### Scenario: Without a run identity the legacy file holds only the latest invocation's keys

- **GIVEN** `ARGO_WORKFLOW_NAME` is unset and `out_dir / "run_manifest.json"` already holds
  `scan_keys: ["scan_1", "scan_2"]`
- **WHEN** a new invocation stages `scan_id=3` successfully
- **THEN** the rewritten `run_manifest.json`'s `scan_keys` are exactly `["scan_3"]`

#### Scenario: A legacy manifest beside a per-run write is neither read nor modified

- **GIVEN** `ARGO_WORKFLOW_NAME` is `"wf-abc123"` and `out_dir / "run_manifest.json"` holds stale
  `scan_keys: ["scan_9"]` from an older run
- **WHEN** the command stages `scan_id=1` successfully
- **THEN** `run_manifest.wf-abc123.json` holds exactly `["scan_1"]`
- **AND** `run_manifest.json` is byte-for-byte unchanged

#### Scenario: A corrupt existing same-name manifest is replaced, not a failure

- **GIVEN** the target manifest path already holds content that is not valid JSON
- **WHEN** a new invocation finishes staging with at least one usable scan
- **THEN** the command writes a valid `RunManifest` over it, and exits `0` when every scan
  succeeded or was skipped

#### Scenario: An invalid run identity fails before any staging

- **WHEN** `ARGO_WORKFLOW_NAME` is set to a value `run_manifest_name_for_writing` rejects (e.g.
  `"../wf"`)
- **THEN** the command exits `1` with an actionable error naming the value, and makes no
  authentication, download, per-scan lock or manifest write — never exit `3`
- **AND** with the same value and an empty `--scan-ids` input, the command still exits `0` and writes
  nothing

#### Scenario: An all-failed batch skips the write, not a crash

- **WHEN** every scan in the batch fails this run
- **THEN** the command exits `3` (the existing all-failed behavior), writes no manifest, leaves any
  existing same-name file untouched, and raises no unhandled error from constructing a
  `RunManifest` with empty `scan_keys`

### Requirement: The RunManifest write is itself protected by a separate short-lived lock

The manifest write SHALL be protected by its own lock at `out_dir/.locks/manifest.lock`, one lock
per `out_dir` whatever the manifest file's name, and distinct from any per-scan lock, so two
invocations finishing around the same time serialize their manifest writes. If this lock cannot be
acquired (held, not stale), the manifest write SHALL fail with a clear, actionable error and SHALL
NOT corrupt or truncate any existing manifest file.

#### Scenario: Manifest-lock contention fails the write without corrupting the existing manifest

- **GIVEN** `out_dir/.locks/manifest.lock` is currently held (not stale) by another invocation
- **WHEN** this invocation finishes staging its scans and attempts to write the `RunManifest`
- **THEN** the command exits non-zero with an actionable error, and any pre-existing manifest file
  remains intact and parseable

#### Scenario: Manifest-lock contention with no existing manifest still fails cleanly

- **GIVEN** `out_dir/.locks/manifest.lock` is currently held (not stale) by another invocation, and
  the manifest file this invocation would write does not exist yet
- **WHEN** this invocation finishes staging its scans and attempts to write the `RunManifest`
- **THEN** the command exits non-zero with an actionable error, and no partial or corrupt manifest
  file is created

#### Scenario: A stale manifest lock is reclaimed, not permanently wedged

- **GIVEN** `out_dir/.locks/manifest.lock` exists with an age strictly greater than the configured
  staleness threshold (e.g. its owning process crashed mid-write)
- **WHEN** a new invocation attempts to write the `RunManifest`
- **THEN** the stale lock is reclaimed and the write proceeds normally

#### Scenario: The manifest lock is released after a successful write

- **WHEN** the `RunManifest` write completes successfully
- **THEN** `out_dir/.locks/manifest.lock` no longer exists
