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
When set, for each frame file found in `<path>` (in a stable, deterministic order), the command
SHALL: call `insert_image_v2_0` to obtain a `cyl_images` row id; upload the frame's bytes to the
`images` storage bucket at object path `cyl-images/cyl-image_{id}_{uuid4()}.png`, where `{id}`
is the row id returned by the RPC and `{uuid4()}` is freshly generated per upload; and then
update that `cyl_images` row's `object_path` to the uploaded path and `status` to `'SUCCESS'`.

#### Scenario: Good scan is fully downloadable after creation

- **WHEN** the user runs `bloomctl cyl create-test-scan --good --frames-dir <dir>` with one frame
  file in `<dir>`
- **THEN** the command calls `insert_image_v2_0` once, uploads the frame's bytes to the `images`
  bucket at `cyl-images/cyl-image_{id}_{uuid}.png`, and updates the row to
  `object_path = cyl-images/cyl-image_{id}_{uuid}.png`, `status = 'SUCCESS'`

#### Scenario: Missing or empty frames directory fails before any RPC call

- **WHEN** `--frames-dir` points to a path that does not exist or contains no frame files
- **THEN** the command exits non-zero with a readable error and makes no `insert_image_v2_0`
  call

### Requirement: --poison and --good are mutually exclusive and one is required

The command SHALL require exactly one of `--poison` or `--good` per invocation. It SHALL create
at most one scan per invocation.

#### Scenario: Both flags rejected

- **WHEN** the user runs the command with both `--poison` and `--good`
- **THEN** the command exits non-zero with a readable error before any RPC call

#### Scenario: Neither flag rejected

- **WHEN** the user runs the command with neither `--poison` nor `--good`
- **THEN** the command exits non-zero with a readable error before any RPC call

### Requirement: The scan's QR code is chosen automatically from existing suffixes

The command SHALL NOT accept a caller-supplied QR code. It SHALL query experiment `12880747`
for the current highest numeric suffix among existing `TEST-E2E-NNN`-style `plant_qr_code`
values, and SHALL use the next integer, zero-padded to match the existing 3-digit width, as the
new scan's `plant_qr_code`. If the insert fails due to a uniqueness conflict on the chosen QR
code (e.g. a concurrent invocation claimed it first), the command SHALL surface a readable,
non-zero-exit error identifying the conflict rather than silently retrying or overwriting.

#### Scenario: Next suffix is chosen

- **WHEN** experiment `12880747`'s highest existing QR code suffix is `009`
- **THEN** the new scan is created with `plant_qr_code = TEST-E2E-010`

#### Scenario: Concurrent claim on the same suffix fails loudly

- **WHEN** two invocations compute the same next suffix and one has already inserted it by the
  time the second attempts its insert
- **THEN** the second invocation's command exits non-zero with a readable conflict error and
  creates no partial state

### Requirement: Output follows the CLI's stdout/stderr and --json conventions

The command SHALL accept `-p/--profile` (defaulting like other `cyl` commands) and `--json`.
Progress and informational text SHALL be written to stderr. With `--json`, a single JSON object
describing the created (or attempted) scan SHALL be written to stdout with no other content on
stdout. Without `--json`, a human-readable summary SHALL be written to stdout.

#### Scenario: --json output is clean on stdout

- **WHEN** the user runs the command with `--json` and it succeeds
- **THEN** stdout contains exactly one parseable JSON object (including at least the created
  scan id and `plant_qr_code`) and any progress messages appear only on stderr
