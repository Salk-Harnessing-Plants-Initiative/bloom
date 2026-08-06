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
invalid frame_numbers, metadata-resolution failure, or a partial frame-download failure) SHALL be
recorded as `failed` with a per-scan error message, and SHALL NOT prevent the remaining scans in
the batch from being staged. The command SHALL exit non-zero if any scan in the batch failed, and
SHALL exit zero if every scan succeeded, was skipped, or the input was empty.

#### Scenario: One bad scan among several does not abort the batch

- **WHEN** a batch of 3 scan_ids includes one scan with zero `cyl_images` rows
- **THEN** the other 2 scans are staged successfully (frames + sidecar present,
  `sleap_roots_predict.discover_scans` accepts both), the bad scan is reported `failed` by name
  with its reason, and the command exits non-zero

#### Scenario: Empty scan_ids input is a no-op, not an error

- **WHEN** `--scan-ids-file`'s content is an empty JSON array (`[]`)
- **THEN** the command creates no output directories, reports zero scans, and exits zero

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

