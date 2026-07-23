## RENAMED Requirements

- FROM: `### Requirement: Blobs pass through without upload`
- TO: `### Requirement: Blob handling defaults to pass-through; --predictions-dir constructs and uploads`

## MODIFIED Requirements

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

## ADDED Requirements

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
