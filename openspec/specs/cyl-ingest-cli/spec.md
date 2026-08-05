# cyl-ingest-cli Specification

## Purpose
TBD - created by archiving change add-cyl-ingest-cli. Update Purpose after archive.
## Requirements
### Requirement: Cyl ingest command reads an envelope from a path or stdin

The `bloomctl` CLI SHALL provide a `cyl ingest-result` command that reads a single per-scan
`ResultEnvelope` (JSON) from a filesystem path argument, or from standard input when the
argument is `-`, and writes it to Bloom by calling the `insert_cyl_result_envelope(jsonb)` RPC
(capability `cyl-trait-writeback`) as `client.rpc("insert_cyl_result_envelope", {"envelope":
<envelope>})`. The command SHALL accept a `--profile` option (defaulting like the other
commands) and authenticate through the existing credentials profile.

#### Scenario: Ingest from a file path

- **WHEN** the user runs `bloomctl cyl ingest-result path/to/scan.result.json` with a valid envelope
  and a profile whose scan is resolvable
- **THEN** the command reads and parses the file, calls the RPC with the envelope under the
  `envelope` argument, and exits zero

#### Scenario: Ingest from stdin

- **WHEN** the user runs `bloomctl cyl ingest-result -` and pipes a valid envelope on stdin
- **THEN** the command reads the envelope from stdin and ingests it identically to the file path
  case

#### Scenario: Unreadable or malformed input

- **WHEN** the path does not exist, the content is not valid JSON, or stdin (`-`) is empty
- **THEN** the command prints a readable error, exits non-zero, and makes no RPC call

#### Scenario: Source-only envelope (no traits or blobs)

- **WHEN** a valid envelope whose `traits` and `blobs` are both empty is ingested and the RPC
  writes only the source row (`trait_count = 0`, `blob_count = 0`)
- **THEN** the command reports success with zero trait/blob counts and exits zero

### Requirement: Envelope is validated before the write and sent unchanged

The command SHALL validate the envelope against `sleap-roots-contracts`
(`ResultEnvelope.model_validate`) as a fail-fast gate before any network call, and SHALL send
the original parsed JSON object to the RPC unchanged (it SHALL NOT re-serialize the envelope
through the contract model), so the producer's `provenance.idempotency_key` is preserved exactly.

#### Scenario: Valid envelope is sent verbatim

- **WHEN** a schema-valid envelope is ingested
- **THEN** the object passed to the RPC equals the originally parsed JSON (same
  `provenance.idempotency_key`, no model-derived substitution)

#### Scenario: Schema-invalid envelope fails fast

- **WHEN** the envelope does not conform to `ResultEnvelope`
- **THEN** the command reports a readable validation error and exits non-zero **before** any
  authentication or RPC call

#### Scenario: Validation gate is stricter than the RPC

- **WHEN** an envelope omits a provenance field that `ResultEnvelope` requires but the RPC does
  not read (e.g. `provenance.inputs.images_checksum` or `provenance.params`)
- **THEN** the command rejects it at the validation gate with a readable message and exits
  non-zero **before** authenticating or calling the RPC

### Requirement: Re-ingest is a benign, distinctly-reported no-op

The command SHALL report the RPC's first-writer-wins no-op — `was_noop=true`, which the RPC
returns without raising for an already-ingested envelope — as a success distinct from a real
error, exiting zero. Re-ingesting the same envelope therefore MUST NOT be reported as a failure.

#### Scenario: First ingest of an envelope

- **WHEN** the RPC returns `was_noop=false`
- **THEN** the command prints a summary indicating the envelope was ingested (including the
  `source_id`) and exits zero

#### Scenario: Re-ingest of the same envelope

- **WHEN** the RPC returns `was_noop=true` (with a null `scan_id`, per `cyl-trait-writeback`)
- **THEN** the command prints an "already ingested" message (naming the `source_id`) that is
  visibly not an error, does not depend on `scan_id` being present, and exits zero

### Requirement: RPC validation failures map to actionable messages

The command SHALL translate the RPC's `RAISE EXCEPTION` validation errors into readable,
actionable CLI messages and exit non-zero, and SHALL surface unrecognized RPC errors verbatim
rather than suppressing them.

#### Scenario: Image ids do not resolve to exactly one scan

- **WHEN** the RPC rejects the envelope because `inputs.image_ids` resolve to no scan or to
  more than one scan (e.g. `no image_ids…`, `unresolvable image_ids: matched X of Y…`, or
  `image_ids resolve to N scans, expected exactly 1`)
- **THEN** the command prints an actionable message that names the profile/server in use and
  explains that the scan's images must already exist in `cyl_images` on that Bloom, and exits
  non-zero

#### Scenario: Contract version mismatch

- **WHEN** the RPC rejects the envelope's `contract_version`
- **THEN** the command reports the version the server expects and the version the envelope
  carried, and exits non-zero

#### Scenario: Other validation failures

- **WHEN** the RPC raises for an empty `idempotency_key`, a `scan_key` disagreement, or other
  documented validation
- **THEN** the command surfaces the failure with field context and exits non-zero

#### Scenario: Unknown RPC error

- **WHEN** the RPC returns an error the command does not specifically recognize
- **THEN** the command surfaces the original message and exits non-zero (no silent success)

### Requirement: Authentication uses an existing profile and requires write access

The command SHALL authenticate using an existing `bloomctl` credentials profile (interactive
login), and SHALL surface a clear error when credentials are missing/invalid or when the
authenticated role lacks `EXECUTE` on the RPC (granted to `bloom_writer` / `bloom_admin` /
`service_role`). Non-interactive/scoped credentials are out of scope for this capability.

#### Scenario: Missing or invalid credentials

- **WHEN** the selected profile has no stored credentials or they fail sign-in
- **THEN** the command prints guidance to run `bloomctl login` and exits non-zero

#### Scenario: Authenticated role lacks write access

- **WHEN** the authenticated profile lacks `EXECUTE` on `insert_cyl_result_envelope`
- **THEN** the resulting permission error is surfaced clearly and the command exits non-zero

### Requirement: Machine-readable result output

The command SHALL support a `--json` flag that writes the RPC's return summary — the `jsonb`
object defined by the `cyl-trait-writeback` capability (`source_id`, `scan_id`, `trait_count`,
`blob_count`, `was_noop`; `scan_id` is null on a no-op re-delivery) — to standard output as JSON
for programmatic consumption (e.g. the A4 write-back step capturing `source_id`); without the
flag it SHALL print a human-readable summary. The command SHALL NOT redefine or reshape the
RPC's return object.

#### Scenario: JSON output on first ingest

- **WHEN** the user passes `--json` on a first ingest
- **THEN** stdout contains the parseable RPC result object, including `source_id` and
  `was_noop=false`

#### Scenario: JSON output on re-ingest

- **WHEN** the user passes `--json` re-ingesting an already-ingested envelope
- **THEN** stdout contains the parseable RPC result object with `was_noop=true` and `source_id`
  (a null `scan_id` is tolerated), and the command exits zero

#### Scenario: Default human-readable output

- **WHEN** the user omits `--json`
- **THEN** stdout contains a human-readable summary line rather than raw JSON

### Requirement: Blob handling defaults to pass-through; --predictions-dir constructs and uploads

The command SHALL forward the envelope's `blobs` array to the RPC unchanged,
making no object-storage upload, when `--predictions-dir` is omitted. When
`--predictions-dir <dir>` is given, the command SHALL read
`<dir>/{scan_key}.predictions.json` (a `PredictionManifest` per
`sleap_roots_contracts` v0.1.0a5+, using the envelope's
`provenance.scan_key`), and for each `PredictionArtifact` SHALL construct a
`BlobRef` (`kind="predictions_slp"`, `root_type`, `scan_key`, `checksum`,
`file_size` copied from the artifact), upload the referenced `.slp` bytes to
the `cyl-intermediates` storage bucket, and populate `s3_location` — before
merging the result into the envelope's `blobs` array and calling the RPC. If
the incoming envelope already contains a `blobs` entry for the same
`(root_type, scan_key)` as one `--predictions-dir` would construct, the command
SHALL fail fast with an actionable error rather than silently overwriting or
duplicating it.

#### Scenario: No predictions-dir, envelope carrying blobs (pass-through, unchanged)

- **WHEN** an envelope with a non-empty `blobs` array is ingested without
  `--predictions-dir`
- **THEN** the `blobs` entries are included in the RPC payload as-is and no
  object-storage upload is attempted

#### Scenario: predictions-dir constructs and uploads blobs

- **WHEN** `--predictions-dir` points at a directory containing
  `{scan_key}.predictions.json` with N artifacts, and the envelope's `blobs`
  array is empty
- **THEN** the command uploads each artifact's `.slp` bytes to the
  `cyl-intermediates` bucket, builds N `BlobRef` entries with `s3_location`
  populated, and calls the RPC with those blobs

#### Scenario: Conflicting pre-existing blob entry

- **WHEN** the envelope already has a `blobs` entry for the same
  `(root_type, scan_key)` that `--predictions-dir` would also construct
- **THEN** the command fails fast with an actionable error before any upload or
  RPC call

### Requirement: A missing predictions manifest or artifact file fails fast

When `--predictions-dir <dir>` is given, the command SHALL fail fast with an
actionable error, before any upload or RPC call, if `<dir>/{scan_key}.predictions.json` does not exist, is not valid JSON, or does not conform to
the expected manifest shape; and SHALL likewise fail fast, naming the missing
file, if any artifact's referenced `.slp` file does not exist on disk.

#### Scenario: Manifest file missing

- **WHEN** `--predictions-dir` is given but `<dir>/{scan_key}.predictions.json`
  does not exist
- **THEN** the command exits non-zero with an actionable error naming the
  expected path, before any upload or RPC call

#### Scenario: Manifest file malformed

- **WHEN** the manifest file exists but is not valid JSON or does not conform
  to the expected `PredictionManifest` shape
- **THEN** the command exits non-zero with an actionable error, before any
  upload or RPC call

#### Scenario: Referenced .slp file missing from disk

- **WHEN** the manifest references a `.slp` file that does not exist on disk
- **THEN** the command exits non-zero, naming the missing file, before any
  upload or RPC call

### Requirement: Blob checksum integrity is verified before upload

The command SHALL recompute the sha256 checksum of each `.slp` file referenced by a `PredictionArtifact` from disk before uploading it, and compare it to
the artifact's declared `checksum`. On mismatch, the command SHALL fail fast
(no upload, no RPC call) with an actionable error naming the file and both
checksums.

#### Scenario: Checksum matches

- **WHEN** the on-disk `.slp`'s sha256 matches the manifest's declared
  checksum
- **THEN** the upload proceeds

#### Scenario: Checksum mismatch

- **WHEN** the on-disk `.slp`'s sha256 does not match the manifest's declared
  checksum
- **THEN** the command exits non-zero before uploading anything or calling the
  RPC, naming the file and both checksums

### Requirement: Blob upload is idempotent

The command SHALL derive each blob's object-storage path deterministically
from `scan_key`, the envelope's `provenance.idempotency_key`, `kind`, and
`root_type` (not `source_id`, which is unknown until the RPC responds). Before
uploading, the command SHALL check whether an object already exists at that
path; if it exists and its checksum matches the artifact's declared checksum,
the command SHALL skip the upload and reuse the existing object's location. If
an object exists at that path with a different checksum, the command SHALL
fail fast rather than overwrite it.

#### Scenario: First upload

- **WHEN** no object exists yet at the derived path
- **THEN** the command uploads the bytes and populates `s3_location` with the
  new object's path

#### Scenario: Retry after a partial failure (same run)

- **WHEN** the command is re-run with the same envelope and predictions-dir
  after a prior partial failure, and some blobs were already uploaded
- **THEN** already-uploaded blobs (matching checksum) are skipped and only the
  remaining blobs are uploaded

#### Scenario: Path collision with different content

- **WHEN** an object already exists at the derived path with a checksum that
  does not match the artifact currently being uploaded
- **THEN** the command fails fast with an actionable error identifying the
  conflicting path, rather than overwriting the existing object

### Requirement: A failed blob upload aborts before the RPC call

The command SHALL NOT call `insert_cyl_result_envelope` for an envelope being processed with `--predictions-dir` if any blob fails to upload or fails its
checksum verification; it SHALL instead report which blob(s) failed and SHALL
exit non-zero. The operator MAY re-run the same command; per the
idempotent-upload requirement, already-succeeded blobs are skipped on retry.

#### Scenario: One blob upload fails

- **WHEN** one of several blobs for a scan fails to upload (e.g. a transient
  storage error)
- **THEN** the command does not call the RPC, reports the failing blob(s), and
  exits non-zero, leaving already-uploaded blobs in place for a cheap retry

