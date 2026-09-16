## ADDED Requirements

### Requirement: An already-ingested envelope skips blob upload entirely

Before constructing or uploading any blob, the command SHALL check whether
`cyl_trait_sources` already holds the envelope's `provenance.idempotency_key`. When it
does, the RPC's first-writer-wins gate will discard this delivery's `blobs` array
without writing it (see `cyl-trait-writeback`), so the command SHALL skip reading the
predictions manifest, verifying checksums, and uploading bytes, and SHALL proceed
directly to the RPC call.

The command SHALL still call `insert_cyl_result_envelope` on this path: the RPC's
`was_noop` branch performs the `cyl_pipeline_run_scans` status update, so skipping the
call would leave dispatched scans stranded at `queued`.

The check SHALL fail open rather than fail the envelope: any error reading
`cyl_trait_sources` — including a permission error when the column grant has not yet
been applied — SHALL be treated as "not already ingested", so the command's behaviour is
never worse than it was without the check.

This requirement exists because the producer's `.slp` output is not byte-reproducible:
the same inputs yield the same `idempotency_key` but different bytes, and the object
path embeds that key, so a recompute targets an occupied address and the strict upload
fails before the lenient RPC gate is ever reached
(sleap-roots-pipeline#76).

#### Scenario: Re-delivery after a recompute that produced different bytes

- **WHEN** an envelope whose `idempotency_key` is already present in `cyl_trait_sources`
  is delivered with `--predictions-dir` holding `.slp` files whose bytes differ from
  those already stored at the derived object path
- **THEN** no upload is attempted, the RPC is still called and returns `was_noop=true`,
  the command exits zero, and the previously stored bytes are left untouched

#### Scenario: A first delivery is unaffected

- **WHEN** the envelope's `idempotency_key` is not present in `cyl_trait_sources`
- **THEN** blobs are constructed, verified, and uploaded exactly as before, and the RPC
  is called with the merged `blobs` array

#### Scenario: The check itself fails

- **WHEN** reading `cyl_trait_sources` raises — a permission error because the
  `idempotency_key` column grant has not been applied yet, or any transient error
- **THEN** the command proceeds to construct and upload blobs as it would without the
  check, and does not report the envelope as failed on account of the check

## MODIFIED Requirements

### Requirement: Blob upload is idempotent

The command SHALL derive each blob's object-storage path deterministically
from `scan_key`, the envelope's `provenance.idempotency_key`, `kind`, and
`root_type` (not `source_id`, which is unknown until the RPC responds). Before
uploading, the command SHALL check whether an object already exists at that
path; if it exists and its checksum matches the artifact's declared checksum,
the command SHALL skip the upload and reuse the existing object's location. If
an object exists at that path with a different checksum, the command SHALL
fail fast rather than overwrite it.

This path-level collision check is reached only for a delivery whose
`idempotency_key` is not already in `cyl_trait_sources` — an already-ingested envelope
skips blob handling entirely, before this check runs (see "An already-ingested envelope
skips blob upload entirely"). A divergent-checksum collision therefore indicates bytes
at that address belonging to no ingested source — for example a delivery that uploaded
and then failed before reaching the RPC — which the command cannot distinguish from a
referenced blob and MUST NOT overwrite.

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
  conflicting path and naming the manual recovery, rather than overwriting the
  existing object
