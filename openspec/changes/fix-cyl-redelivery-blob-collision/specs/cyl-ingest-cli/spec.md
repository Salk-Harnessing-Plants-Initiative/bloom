## ADDED Requirements

### Requirement: An already-ingested envelope skips blob upload

With `--predictions-dir` given, the command SHALL check whether `cyl_trait_sources` already
holds the envelope's `provenance.idempotency_key` — after constructing the pending blobs, and
before uploading any bytes. Where the key is already present, the RPC's first-writer-wins gate
will discard this delivery's `blobs` array without writing it to the trait or blob tables (see
`cyl-trait-writeback`), so the command SHALL skip the upload and SHALL NOT merge the constructed
blobs into the envelope, proceeding directly to the RPC call.

The check SHALL be performed after manifest loading and blob construction, so that every
fail-fast guarantee those steps provide — a missing or malformed manifest, a missing `.slp` file,
an `slp_path` resolving outside the predictions directory, a conflicting pre-existing `blobs`
entry — continues to apply unchanged on every delivery, whether or not it is a re-delivery.

The command SHALL still call `insert_cyl_result_envelope` on the skip path. The RPC is the
only component that can detect a same-key-different-scan delivery, and the gate's read is
non-transactional, so its answer is advisory: the RPC remains the authority on whether this
delivery writes anything.

Note for future readers: this call does **not** rescue a `cyl_pipeline_run_scans` row stranded
at `queued`. `source_id` is written only by the RPC's non-no-op path, in the same statement
that sets `status = 'written'`, so `source_id IS NOT NULL` implies the row is already written;
the no-op branch's `source_id`-keyed UPDATE can therefore only re-touch a row that needs no
rescue. An earlier draft of this requirement justified the call on that basis, which was
wrong. The call is still required, for the reason above.

The check SHALL fail open rather than fail the envelope: any error reading `cyl_trait_sources` —
including a permission error when the column grant has not yet been applied, and including
transport-level errors that are not `postgrest.APIError` — SHALL be treated as "not already
ingested", so the command's behaviour is never worse than it was without the check. Because such
a fallback silently restores the original defect, the command SHALL emit a warning identifying
the degraded check and the likely-missing grant, at a level that is visible without the caller
configuring logging.

This requirement exists because the producer's `.slp` output is not byte-reproducible, so a
recompute targets an occupied object address and the strict upload fails before the lenient RPC
gate is reached (sleap-roots-pipeline#76).

#### Scenario: Re-delivery after a recompute that produced different bytes

- **WHEN** an envelope whose `idempotency_key` is already present in `cyl_trait_sources` is
  delivered with `--predictions-dir` holding `.slp` files whose bytes differ from those already
  stored at the derived object path
- **THEN** no upload is attempted, the constructed blobs are not merged into the envelope, the
  RPC is still called and returns `was_noop=true`, and the previously stored bytes are left
  untouched
- **AND** the delivery is reported `skipped`, exiting zero — except where the RPC also reports
  `status_update_matched=false`, which a re-delivery dispatched under a *different*
  `ARGO_WORKFLOW_NAME` currently always does; that case is reported `failed` with
  `retriable=false` by the `cyl-pipeline-run-scan-status` contract, and reconciling the two
  contracts is tracked as a follow-up (see `design.md` Risks)

#### Scenario: A first delivery is unaffected

- **WHEN** `--predictions-dir` is given and the envelope's `idempotency_key` is not present in
  `cyl_trait_sources`
- **THEN** blobs are constructed, verified, and uploaded exactly as before, and the RPC is
  called with the merged `blobs` array

#### Scenario: Pass-through mode performs no lookup

- **WHEN** `--predictions-dir` is omitted
- **THEN** no `cyl_trait_sources` lookup is performed and the envelope's `blobs` array is
  forwarded to the RPC unchanged, exactly as before

#### Scenario: A missing manifest still fails fast, even for an already-ingested envelope

- **WHEN** `--predictions-dir` is given, the envelope's `idempotency_key` is already present in
  `cyl_trait_sources`, and `<dir>/{scan_key}.predictions.json` does not exist
- **THEN** the command still fails fast naming the expected path, because the check runs after
  the manifest load, not before it

#### Scenario: An empty or absent idempotency key fails before the check

- **WHEN** `--predictions-dir` is given and the envelope's `provenance.idempotency_key` is empty
  or absent
- **THEN** the command fails with the existing actionable error and no `cyl_trait_sources`
  lookup is issued

#### Scenario: The check itself fails

- **WHEN** reading `cyl_trait_sources` raises — a `postgrest.APIError` carrying a 42501 because
  the `idempotency_key` column grant has not been applied, or a transport error such as
  `httpx.ConnectError`
- **THEN** the command proceeds to upload blobs as it would without the check, does not report
  the envelope as failed on account of the check, **and** emits a warning naming the degraded
  check and the likely-missing grant

## MODIFIED Requirements

### Requirement: Blob handling defaults to pass-through; --predictions-dir constructs and uploads

The command SHALL forward the envelope's `blobs` array to the RPC unchanged,
making no object-storage upload, when `--predictions-dir` is omitted. When
`--predictions-dir <dir>` is given, the command SHALL read
`<dir>/{scan_key}.predictions.json` (a `PredictionManifest` per
`sleap_roots_contracts` v0.1.0a5+, using the envelope's
`provenance.scan_key`), and for each `PredictionArtifact` SHALL construct a
`BlobRef` (`kind="predictions_slp"`, `root_type`, `scan_key`, `checksum`,
`file_size` copied from the artifact), and — unless the envelope's
`idempotency_key` is already present in `cyl_trait_sources`, in which case the upload and the
merge are both skipped (see "An already-ingested envelope skips blob upload") — upload the
referenced `.slp` bytes to the `cyl-intermediates` storage bucket, and populate `s3_location`,
before merging the result into the envelope's `blobs` array and calling the RPC. If
the incoming envelope already contains a `blobs` entry for the same
`(root_type, scan_key)` as one `--predictions-dir` would construct, the command
SHALL fail fast with an actionable error rather than silently overwriting or
duplicating it — on every delivery, including a re-delivery, because construction precedes the
already-ingested check.

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

### Requirement: Blob checksum integrity is verified before upload

The command SHALL recompute the sha256 checksum of each `.slp` file referenced by a `PredictionArtifact` from disk before uploading it, and compare it to
the artifact's declared `checksum`. On mismatch, the command SHALL fail fast
(no upload, no RPC call) with an actionable error naming the file and both
checksums.

Verification is part of the upload step, so it does not run when the upload is skipped because
the envelope's `idempotency_key` is already present in `cyl_trait_sources` (see "An
already-ingested envelope skips blob upload"). That is intentional: on a re-delivery the local
bytes are never stored, so their integrity is not a property the delivery can affect, and
failing on them would reintroduce the non-idempotent re-delivery this capability exists to
avoid.

#### Scenario: Checksum matches

- **WHEN** the on-disk `.slp`'s sha256 matches the manifest's declared
  checksum
- **THEN** the upload proceeds

#### Scenario: Checksum mismatch

- **WHEN** the on-disk `.slp`'s sha256 does not match the manifest's declared
  checksum, and the envelope's `idempotency_key` is not present in `cyl_trait_sources`
- **THEN** the command exits non-zero before uploading anything or calling the
  RPC, naming the file and both checksums

#### Scenario: Checksum mismatch on an already-ingested envelope is not reached

- **WHEN** the on-disk `.slp`'s sha256 does not match the manifest's declared checksum and the
  envelope's `idempotency_key` is already present in `cyl_trait_sources`
- **THEN** no verification is performed, no upload is attempted, and the delivery is reported as
  the RPC's benign no-op

### Requirement: Blob upload is idempotent

The command SHALL derive each blob's object-storage path deterministically
from `scan_key`, the envelope's `provenance.idempotency_key`, `kind`, and
`root_type` (not `source_id`, which is unknown until the RPC responds). Before
uploading, the command SHALL check whether an object already exists at that
path; if it exists and its checksum matches the artifact's declared checksum,
the command SHALL skip the upload and reuse the existing object's location. If
an object exists at that path with a different checksum, the command SHALL
fail fast rather than overwrite it.

This path-level collision check is reached only for a delivery whose `idempotency_key` is not
already in `cyl_trait_sources`. A divergent-checksum collision therefore indicates bytes at that
address belonging to no ingested source — for example a delivery that uploaded and then failed
before reaching the RPC — which the command cannot distinguish from a referenced blob and MUST
NOT overwrite. The error SHALL name both the conflicting path and an identity able to remove the
object: the write-back identity itself holds no DELETE on the `cyl-intermediates` bucket, so
recovery requires `bloom_admin`, `service_role`, or an operator acting directly on storage.

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
  does not match the artifact currently being uploaded, and the envelope's
  `idempotency_key` is not present in `cyl_trait_sources`
- **THEN** the command fails fast with an actionable error identifying the
  conflicting path and naming an identity able to remove it, rather than
  overwriting the existing object

### Requirement: A failed blob upload aborts before the RPC call

The command SHALL NOT call `insert_cyl_result_envelope` for an envelope being processed with `--predictions-dir` if any blob fails to upload or fails its
checksum verification; it SHALL instead report which blob(s) failed and SHALL
exit non-zero. The operator MAY re-run the same command; already-succeeded blobs whose bytes are
unchanged are skipped on retry. If the producer regenerated its artifacts between attempts, the
retry instead collides at the derived path — the failed delivery wrote no source row, so the
already-ingested check does not fire — and requires the recovery the collision error names.

#### Scenario: One blob upload fails

- **WHEN** one of several blobs for a scan fails to upload (e.g. a transient
  storage error)
- **THEN** the command does not call the RPC, reports the failing blob(s), and
  exits non-zero, leaving already-uploaded blobs in place for a cheap retry

#### Scenario: Retry after a failed upload whose artifacts were regenerated

- **WHEN** a delivery failed after uploading some blobs and before the RPC call, and the
  producer has since recomputed its `.slp` files at the same `idempotency_key`
- **THEN** the retry fails at the path collision rather than succeeding, because no source row
  exists for the already-ingested check to find

### Requirement: Re-ingest is a benign, distinctly-reported no-op

The command SHALL report the RPC's first-writer-wins no-op — `was_noop=true`, which the RPC
returns without raising for an already-ingested envelope — as a success distinct from a real
error, exiting zero. Re-ingesting the same envelope therefore MUST NOT be reported as a failure.
This SHALL hold end to end, not only for the RPC's response: a re-delivery whose producer
regenerated its artifacts MUST NOT fail at the blob-upload step before the RPC's gate is
reached.

#### Scenario: First ingest of an envelope

- **WHEN** the RPC returns `was_noop=false`
- **THEN** the command prints a summary indicating the envelope was ingested (including the
  `source_id`) and exits zero

#### Scenario: Re-ingest of the same envelope

- **WHEN** the RPC returns `was_noop=true` (with a null `scan_id`, per `cyl-trait-writeback`)
- **THEN** the command prints an "already ingested" message (naming the `source_id`) that is
  visibly not an error, does not depend on `scan_id` being present, and exits zero

#### Scenario: Re-delivery whose producer recomputed its artifacts

- **WHEN** an already-ingested envelope is re-delivered with `--predictions-dir` containing
  `.slp` bytes that differ from those already stored at the derived path
- **THEN** the command still reports a benign, distinctly-reported no-op and exits zero, because
  the upload is skipped before the divergence can be observed
