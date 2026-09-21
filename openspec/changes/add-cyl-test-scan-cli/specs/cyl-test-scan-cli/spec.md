## ADDED Requirements

### Requirement: create-test-scan is hardcoded to experiment 12880747 and guards it live

The `bloomctl` CLI SHALL provide a `cyl create-test-scan` command that operates only against
experiment `12880747` ("A4-PIPELINE-E2E-TEST"). The command SHALL NOT accept an experiment
override option. Before performing any mutation, the command SHALL query the target Supabase
instance for experiment `12880747` and verify its name starts with `A4-PIPELINE-E2E-TEST`. If the
experiment does not exist, or its name does not match, the command SHALL raise a
`click.ClickException`, exit non-zero, and make no writes.

#### Scenario: Guard passes against the real test experiment

- **WHEN** the command runs against a profile whose experiment `12880747` is named
  `A4-PIPELINE-E2E-TEST (synthetic -- safe to break/delete)`
- **THEN** the guard passes and the command proceeds to create the scan

#### Scenario: Guard refuses a mismatched or missing experiment

- **WHEN** experiment `12880747` does not exist in the target database, or exists with a
  different name
- **THEN** the command exits non-zero with a readable error and performs no `insert_image_v2_0`
  call, no storage upload, and no `cyl_images` update

### Requirement: Invocations are serialized with a same-machine file lock

Before selecting a QR-code suffix or calling `insert_image_v2_0`, the command SHALL acquire an
exclusive lock via `bloomctl.cyl._locks.acquire_lock`, with `staleness_seconds =
DEFAULT_LOCK_STALENESS_SECONDS` (900), at a fixed, well-known path shared by every invocation of
this command (`~/.bloom/.locks/cyl-create-test-scan-12880747.lock`), and SHALL hold it in one
single `with` block until the scan's creation (every frame's RPC call, frame-count check,
upload, and row update) has fully completed or failed. If the lock is already held by another
live invocation, the command SHALL exit non-zero immediately with a readable error, performing no
RPC call, rather than blocking or retrying. This guarantee holds only while a live invocation's
actual runtime stays under the configured staleness threshold; an invocation exceeding it can
have its lock reclaimed by a peer, which the frame-count check (below) is the last line of
defense against.

#### Scenario: A concurrent invocation is refused, not merged

- **WHEN** a second `create-test-scan` invocation starts while a first one still holds the lock
- **THEN** the second invocation exits non-zero immediately with a message identifying the lock
  as held, and makes no `insert_image_v2_0` call

### Requirement: An RPC result indicating a pre-existing SUCCESS row is a hard failure

The command SHALL treat an `insert_image_v2_0` result of `NULL` (indicating the resolved
`cyl_images` row is already `status = 'SUCCESS'`) as an unexpected failure, not a benign no-op.
It SHALL abort before any storage call, for both `--poison` and `--good`, and for every frame
within a multi-frame `--good` scan, reporting the QR code and frame number that produced the
`NULL` result.

#### Scenario: NULL from the RPC aborts before upload

- **WHEN** `insert_image_v2_0` returns `NULL` for a frame
- **THEN** the command exits non-zero, names the QR code and frame number in its error, and makes
  no storage upload or `cyl_images` update for that frame

### Requirement: --poison mode creates an undownloadable scan

The command SHALL support a `--poison` flag. When set, the command SHALL call
`insert_image_v2_0` for exactly one frame and perform no storage upload and no subsequent
`cyl_images` update — leaving the row exactly as the RPC created it (`status = 'PENDING'`,
`object_path` `NULL`).

#### Scenario: Poison scan has no object_path

- **WHEN** the user runs `bloomctl cyl create-test-scan --poison`
- **THEN** the command calls `insert_image_v2_0` once with `frame_number_ = 1`, makes no storage
  API call, and the resulting `cyl_images` row has `object_path IS NULL` and
  `status = 'PENDING'`

### Requirement: --good mode creates a scan with real, downloadable imagery

The command SHALL support a `--good` flag paired with a required `--frames-dir <path>` option.
`--frames-dir` SHALL NOT be accepted together with `--poison`. For each frame file found directly
in `<path>` (image files only, in ascending filename order, numbered `frame_number_ = 1, 2, 3,
...` in that order), processed one at a time and stopping at the first failure (no further
frames are attempted once one fails), the command SHALL: reject the file if it is smaller than 1
KiB (a mechanical guard against blank/placeholder frames, not a content classifier — a real
root-scan photograph is expected to exceed this trivially); otherwise call `insert_image_v2_0` to
obtain a `cyl_images` row id; confirm the target scan's frame count (resolved from the returned
id) equals the number of frames processed so far in this invocation, aborting loudly on a
mismatch rather than uploading against an unexpected existing scan; upload the frame's bytes to
the `images` storage bucket at object path `cyl-images/cyl-image_{id}_{uuid4()}.png`, where `{id}`
is the row id returned by the RPC and `{uuid4()}` is freshly generated per upload, retrying once
on a transient (429/5xx) storage error and raising immediately on any other upload error; and
then update that `cyl_images` row's `object_path` to the uploaded path and `status` to
`'SUCCESS'`.

#### Scenario: Good scan is fully downloadable after creation

- **WHEN** the user runs `bloomctl cyl create-test-scan --good --frames-dir <dir>` with one
  frame file at least 1 KiB in `<dir>`
- **THEN** the command calls `insert_image_v2_0` once, uploads the frame's bytes to the `images`
  bucket at `cyl-images/cyl-image_{id}_{uuid}.png`, and updates the row to
  `object_path = cyl-images/cyl-image_{id}_{uuid}.png`, `status = 'SUCCESS'`

#### Scenario: A frame below the size floor is rejected before any RPC call

- **WHEN** a file in `--frames-dir` is smaller than 1 KiB
- **THEN** the command exits non-zero with an error naming the file and explaining the size
  floor, and makes no `insert_image_v2_0` call for that file

#### Scenario: Missing or empty frames directory fails before any RPC call

- **WHEN** `--frames-dir` points to a path that does not exist or contains no image files
- **THEN** the command exits non-zero with a readable error and makes no `insert_image_v2_0`
  call

#### Scenario: A row-update failure after a successful upload is reported, not silently swallowed

- **WHEN** a frame's bytes upload successfully but the subsequent `cyl_images` row update
  (`object_path`/`status`) raises
- **THEN** the command exits non-zero with an error identifying the frame and noting the object
  was uploaded but the row was not updated, and processes no further frames

#### Scenario: Non-image files in the directory are ignored, not counted as frames

- **WHEN** `--frames-dir` contains both image files and non-image files (e.g. a stray `.txt`)
- **THEN** only the image files are processed as frames, in ascending filename order, and the
  non-image files are neither uploaded nor counted

### Requirement: Identity fields use fixed synthetic sentinel values, never real staff or accession data

The command SHALL pass fixed, dedicated sentinel values for `phenotyper_name`,
`phenotyper_email`, `scientist_name`, `scientist_email`, and `accession_name` on every call to
`insert_image_v2_0` — never values copied from an existing scan's row — because `phenotypers`,
`cyl_scientists`, and `accessions` are upserted by the RPC on a global natural key with no
experiment scoping, and copying forward an existing value risks silently attaching a synthetic
scan to a real staff member's or real accession's row. `device_name` is explicitly excluded from
this requirement: it is not upserted by the RPC (a non-existent scanner name makes the call
raise), so the command SHALL instead pass the real, existing scanner name sourced per the
following requirement.

#### Scenario: Sentinel identity values are used regardless of profile or prior scans

- **WHEN** the command creates any scan (poison or good)
- **THEN** the RPC call's `phenotyper_email` and `scientist_email` end in `.invalid`, and
  `accession_name` is the fixed synthetic sentinel string, regardless of what values any
  existing `TEST-E2E-*` scan carries

### Requirement: Wave/plant-batch metadata, including device_name, is sourced from an existing scan

The command SHALL pass fixed values for `species_common_name`, `wave_number`, `germ_day`,
`germ_day_color`, `plant_age_days`, `date_scanned_`, and `device_name` on every call to
`insert_image_v2_0`, sourced from an existing `TEST-E2E-*` scan's real values and recorded in
`design.md`. `device_name` SHALL be a real, currently-existing `cyl_scanners.name` value — never
a synthetic/invented string — because the RPC treats a non-matching `device_name` as a hard
error, not an upsertable field.

#### Scenario: device_name matches a real scanner

- **WHEN** the command creates any scan (poison or good)
- **THEN** the RPC call's `device_name` equals an existing `cyl_scanners.name` value, and the
  call does not raise a "Scanner does not exist" error

### Requirement: --poison and --good are mutually exclusive and one is required

The command SHALL require exactly one of `--poison` or `--good` per invocation. It SHALL create
at most one scan per invocation.

#### Scenario: Both flags rejected

- **WHEN** the user runs the command with both `--poison` and `--good`
- **THEN** the command exits non-zero with a readable error before any RPC call

#### Scenario: Neither flag rejected

- **WHEN** the user runs the command with neither `--poison` nor `--good`
- **THEN** the command exits non-zero with a readable error before any RPC call

#### Scenario: --frames-dir with --poison is rejected

- **WHEN** the user runs the command with `--poison --frames-dir <dir>`
- **THEN** the command exits non-zero with a readable error before any RPC call

### Requirement: The scan's QR code is chosen automatically from existing suffixes

The command SHALL NOT accept a caller-supplied QR code. While holding the lock described above,
it SHALL query experiment `12880747` for the current highest numeric suffix among existing
`TEST-E2E-NNN`-style `plant_qr_code` values, and SHALL use the next integer, zero-padded to
match the existing 3-digit width, as the new scan's `plant_qr_code`.

#### Scenario: Next suffix is chosen

- **WHEN** experiment `12880747`'s highest existing QR code suffix is `009`
- **THEN** the new scan is created with `plant_qr_code = TEST-E2E-010`

### Requirement: Output follows the CLI's stdout/stderr and --json conventions

The command SHALL accept `-p/--profile` (defaulting like other `cyl` commands, forwarded to
`_authed_client`) and `--json`. Progress and informational text SHALL be written to stderr. With
`--json`, a single JSON object describing the created scan SHALL be written to stdout with no
other content on stdout. Without `--json`, a human-readable summary naming the scan id and
`plant_qr_code` SHALL be written to stdout.

#### Scenario: --json output is clean on stdout

- **WHEN** the user runs the command with `--json` and it succeeds
- **THEN** stdout contains exactly one parseable JSON object (including at least the created
  scan id and `plant_qr_code`) and any progress messages appear only on stderr

#### Scenario: Human-readable summary without --json

- **WHEN** the user runs the command without `--json` and it succeeds
- **THEN** stdout contains a human-readable line naming the created scan's id and
  `plant_qr_code`
