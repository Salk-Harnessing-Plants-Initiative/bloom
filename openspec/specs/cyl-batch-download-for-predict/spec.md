# cyl-batch-download-for-predict Specification

## Purpose
TBD - created by archiving change add-cyl-batch-commands. Update Purpose after archive.
## Requirements
### Requirement: Batch download-for-predict command stages every scan_id in one invocation

The `bloomctl` CLI SHALL provide a `cyl batch-download-for-predict <out_dir>` command that stages
every scan given via `--scan-ids-file` (a JSON array of integers, read from a filesystem path, or
from standard input when the option's value is `-`) or `--scan-ids` (a comma-separated list of
integers) into the layout `sleap_roots_predict.discover_scans` expects: `out_dir/{scan_key}/` per
scan, identical to what `cyl download-for-predict` writes for one scan. Exactly one of
`--scan-ids-file`/`--scan-ids` MUST be given; `out_dir` is the command's only positional argument
(a scan_ids input is deliberately not a positional — see the `cyl-batch-ingest-result` sibling
capability's equivalent design note for why an optional-before-required positional pair doesn't
work in Click). The command SHALL accept `--profile`/`-p` like the existing single-scan command.

#### Scenario: Every scan_id is staged into its own nested directory

- **WHEN** the user runs `bloomctl cyl batch-download-for-predict /tmp/stage --scan-ids-file
  scan_ids.json` where `scan_ids.json` contains `[1, 2, 3]` and all three scans exist
- **THEN** `/tmp/stage/scan_1/`, `/tmp/stage/scan_2/`, `/tmp/stage/scan_3/` are each written with
  their frames and sidecar exactly as `cyl download-for-predict` would write them individually

#### Scenario: scan_ids via stdin

- **WHEN** the user runs `bloomctl cyl batch-download-for-predict /tmp/stage --scan-ids-file -`
  and pipes `[1, 2, 3]` on stdin
- **THEN** all three scans are staged identically to the file-path case

#### Scenario: scan_ids via the comma-separated convenience flag

- **WHEN** the user runs `bloomctl cyl batch-download-for-predict /tmp/stage --scan-ids 1,2,3`
  (no `--scan-ids-file`)
- **THEN** all three scans are staged identically to the JSON-array case

#### Scenario: --scan-ids-file and --scan-ids cannot both be given

- **WHEN** the user passes both `--scan-ids-file` and `--scan-ids`
- **THEN** the command exits non-zero with a `UsageError` before any I/O

#### Scenario: --scan-ids-file and --scan-ids cannot both be omitted

- **WHEN** the user passes neither `--scan-ids-file` nor `--scan-ids`
- **THEN** the command exits non-zero with a `UsageError` before any I/O

### Requirement: Malformed or unreadable scan_ids input is rejected before any staging

The command SHALL exit non-zero with a readable error, and SHALL NOT stage any scan, if
`--scan-ids-file`'s value does not exist, is a directory rather than a file, is not valid JSON, or
parses to something other than a JSON array of integers.

#### Scenario: --scan-ids-file value does not exist or is not a file

- **WHEN** `--scan-ids-file`'s value is a path that does not exist, or is a directory
- **THEN** the command exits non-zero with a readable error before staging any scan

#### Scenario: --scan-ids-file content is not a JSON array of integers

- **WHEN** the content at `--scan-ids-file`'s path (or on stdin) is not valid JSON, or parses to
  something other than an array of integers (e.g. an object, a string, or an array containing a
  non-integer)
- **THEN** the command exits non-zero with a readable error before staging any scan

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

### Requirement: A scan whose stage directory already has a valid sidecar is skipped

The command SHALL skip re-staging a scan (recording it as `skipped`, not re-downloading any
frame) if `out_dir/{scan_key}/{scan_key}.scan_metadata.json` already exists, parses as JSON, and
its `scan_key` field equals `{scan_key}` — the same validity check
`sleap_roots_predict.batch._load_scan` applies when deciding whether a staged scan is usable. If
the sidecar is missing, unparseable, or its `scan_key` field does not match, the command SHALL
treat the scan as not staged and perform a full clear-and-redownload, identical to the existing
single-scan command's unconditional behavior.

#### Scenario: Already-staged scan is skipped without re-downloading

- **WHEN** `out_dir/scan_1/scan_1.scan_metadata.json` already exists with `"scan_key": "scan_1"`
  and the batch command is run again including `scan_id=1`
- **THEN** scan 1 is reported `skipped`, no frame is re-downloaded for it, and its existing
  sidecar and frame files are left untouched

#### Scenario: A malformed existing sidecar is not treated as done

- **WHEN** `out_dir/scan_1/scan_1.scan_metadata.json` exists but is not valid JSON, or its
  `scan_key` field does not equal `"scan_1"`
- **THEN** the command clears `out_dir/scan_1/` and redownloads it in full, the same as if no
  sidecar had existed

### Requirement: Machine-readable batch result output

The command SHALL support a `--json` flag that writes the aggregate batch result to stdout as
JSON — one entry per scan_id with its `scan_key`, `status` (`ok`/`skipped`/`failed`), and `error`
(empty unless `failed`). Without the flag, the command SHALL print a human-readable summary line
(count staged / skipped / failed) plus one line per failed scan naming it and its error.

#### Scenario: JSON output enumerates every scan's status

- **WHEN** the user passes `--json` on a batch with a mix of ok, skipped, and failed scans
- **THEN** stdout contains a parseable JSON array with one entry per scan_id, each carrying its
  `scan_key` and `status`, and `error` populated only for `failed` entries

#### Scenario: Default human-readable output names every failure

- **WHEN** the user omits `--json` and one scan in the batch failed
- **THEN** stdout contains a summary count and a line identifying the failed scan by its
  `scan_key` and error message

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

### Requirement: A per-scan lock prevents two invocations from racing on the same scan

`stage_one_scan` SHALL hold an exclusive lock scoped to a scan's `scan_key`, at
`out_dir/.locks/{scan_key}.lock`, from before checking whether that scan is already staged through
writing its sidecar — a location outside `out_dir/{scan_key}/` itself, so clearing
that scan's directory never removes the lock file. The `out_dir/.locks/` directory SHALL be
created if it does not already exist. If the lock is currently held by another live invocation,
staging that scan_id SHALL fail with a `ScanResult(status="failed")` whose error message names the
lock holder's pid and the lock's age — the failure SHALL be isolated to that scan and SHALL NOT
abort the rest of the batch. Two invocations targeting different scan_ids SHALL NOT contend on
each other's locks. A lock's age exactly equal to the configured staleness threshold SHALL NOT be
treated as stale (only an age strictly greater than the threshold is reclaimable).

#### Scenario: Two invocations racing on the same scan_id do not both stage it

- **GIVEN** a lock for `scan_1` is currently held (not stale) by another invocation
- **WHEN** a second invocation's batch includes `scan_id=1`
- **THEN** that scan is reported `failed` with an error message naming the lock holder's pid and
  the lock's age, and no frame or sidecar write for `scan_1` occurs from the second invocation

#### Scenario: A lock can be acquired even when out_dir/.locks/ doesn't exist yet

- **GIVEN** `out_dir` is brand new and `out_dir/.locks/` does not exist
- **WHEN** the first scan in a batch is staged
- **THEN** the lock is acquired successfully (the `.locks/` directory is created), and staging
  proceeds normally

#### Scenario: A lock aged exactly at the staleness threshold is still contended, not reclaimed

- **GIVEN** a lock file for `scan_1` exists with an age exactly equal to the configured staleness
  threshold (not one second more)
- **WHEN** a new invocation's batch includes `scan_id=1`
- **THEN** the lock is treated as still held (not stale), and that scan is reported `failed` with
  a lock-contention message

#### Scenario: Concurrent invocations on disjoint scan_ids do not contend

- **GIVEN** invocation A is currently staging `scan_1` (holding `scan_1`'s lock)
- **WHEN** invocation B's batch stages `scan_2` at the same time
- **THEN** invocation B's staging of `scan_2` proceeds without being blocked or failed by
  invocation A's lock

#### Scenario: A stale per-scan lock is reclaimed, not permanently wedged

- **GIVEN** a lock file for `scan_1` exists with an `acquired_at` older than the configured
  staleness threshold (e.g. its owning process crashed without releasing it)
- **WHEN** a new invocation's batch includes `scan_id=1`
- **THEN** the stale lock is reclaimed and `scan_1` is staged normally by the new invocation

#### Scenario: The lock is released after successful staging

- **WHEN** a scan stages successfully
- **THEN** its per-scan lock file no longer exists once `stage_one_scan` returns

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

### Requirement: Lock staleness is configurable

`batch-download-for-predict` SHALL accept a `--lock-staleness-seconds` option (default `900`)
controlling the age threshold both the per-scan and manifest locks use to decide a lock is stale
and reclaimable.

#### Scenario: A custom staleness threshold is honored

- **WHEN** the command is run with `--lock-staleness-seconds 5` and a lock file exists with an
  `acquired_at` 10 seconds in the past
- **THEN** that lock is treated as stale and reclaimed

### Requirement: Each scan's frame downloads run through a bounded concurrent worker pool

The `cyl batch-download-for-predict` command SHALL accept a `-n`/`--workers` option (integer,
1-64 inclusive, default 8) controlling how many of each scan's frames download concurrently,
applied independently to every scan in the batch. Scans themselves SHALL continue to be staged one
at a time (this option does not introduce cross-scan concurrency). The per-scan lock
(`out_dir/.locks/{scan_key}.lock`) SHALL be held for the entire duration of that scan's concurrent
frame-fetch pool, exactly as it is held for the sequential case — the pool SHALL be fully complete
before the lock is released, so two invocations racing on the same `scan_id` remain protected the
same way regardless of `--workers`.

#### Scenario: --workers applies per scan across a batch

- **WHEN** the user runs `bloomctl cyl batch-download-for-predict /tmp/stage --scan-ids 1,2
  --workers 4` and both scans have several frames each
- **THEN** each scan's frames download using up to 4 concurrent workers, and both scans are staged
  correctly (frames + sidecar present, matching what `--workers 1` would produce)

#### Scenario: --workers is bounded to 1-64

- **WHEN** the user passes `--workers 0`, `--workers 65`, or a negative value
- **THEN** the command exits non-zero with a usage error before any scan is staged

#### Scenario: The per-scan lock is held for the whole concurrent frame-fetch pool

- **GIVEN** a scan's frame downloads are running concurrently under `--workers 4`
- **WHEN** any one of those concurrent frame downloads is in flight
- **THEN** that scan's per-scan lock file (`out_dir/.locks/{scan_key}.lock`) exists, and it no
  longer exists once the scan finishes staging — the lock's critical section is not shortened,
  lengthened, or exited early by the pool's internal concurrency

#### Scenario: Default worker count matches the single-scan command

- **WHEN** the user omits `--workers`
- **THEN** each scan's frames download with the same default worker count (8) as
  `cyl download-for-predict`'s own default

#### Scenario: A disk-full condition within one scan is isolated to that scan

- **WHEN** writing a frame's bytes to disk fails because the disk is full or the storage quota is
  spent while staging one scan in the batch, at any `--workers` value, with other scans still
  pending
- **THEN** that scan is reported `failed` the same way any other partial frame-download failure is
  reported, and the remaining scans in the batch are still staged normally (a disk-full condition
  in one scan's pool does not abort the batch, and does not affect any other scan's independent
  disk-full tracking)

