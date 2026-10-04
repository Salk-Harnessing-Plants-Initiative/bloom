# cyl-ingest-cli Specification

## Purpose
TBD - created by archiving change add-cyl-ingest-cli. Update Purpose after archive.
## Requirements
### Requirement: Cyl ingest command reads an envelope from a path or stdin

The `bloomctl` CLI SHALL provide a `cyl ingest-result` command that reads a single per-scan
`ResultEnvelope` (JSON) from a filesystem path argument, or from standard input when the
argument is `-`, and writes it to Bloom by calling the `insert_cyl_result_envelope(jsonb, text)` RPC
(capability `cyl-trait-writeback`) as `client.rpc("insert_cyl_result_envelope", {"envelope":
<envelope>, "p_argo_workflow_name": <value or omitted>})`. The `p_argo_workflow_name` key SHALL be
included, set to the run identity `sleap_roots_contracts.pipeline_run_id_from_env()` returns (the
whitespace-stripped `ARGO_WORKFLOW_NAME`), only when that identity is not `None`; when the variable is
unset or blank, the command SHALL omit the key entirely (relying on the RPC's `DEFAULT NULL`) rather
than sending an empty or whitespace string, preserving the existing manual/ad-hoc invocation shape
exactly. This is the same single definition of the run identity `cyl batch-ingest-result` uses to
resolve its run manifest and to reconcile, so a batch's manifest scope, per-scan status updates and
reconciliation can never target different workflow names.
The command SHALL accept a `--profile` option (defaulting like the other commands) and authenticate
through the existing credentials profile. When `p_argo_workflow_name` was supplied and the RPC's
returned `status_update_matched` is `false`, both this command and the shared per-envelope batch
helper (`ingest_one_envelope`, used by `cyl batch-ingest-result`) SHALL report it as a failure
rather than a plain success, with a message that matches the RPC's `was_noop`:

- `was_noop: false` — a delivery that wrote its trait/blob data correctly but whose per-scan status
  linkage was skipped, either because no row matched this scan under this workflow or because the
  matching `cyl_pipeline_run_scans` row was already `'failed'`. The message SHALL say the data was
  written and SHALL NOT assert either cause as the only one.
- `was_noop: true` — an already-ingested envelope that wrote nothing, and whose re-delivery could
  not update this workflow's row for the source's scan. The message SHALL name the existing
  `source_id`, SHALL say that nothing was written, and MUST NOT claim that any trait or blob data
  was written by this delivery.

#### Scenario: A status linkage mismatch is reported as a failure, not a silent success

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` set, the envelope's trait/source/blob rows
  are written successfully (`was_noop: false`), but the RPC's returned `status_update_matched` is
  `false`
- **THEN** the command still prints/emits the real, successful write outcome, but then reports a
  failure explaining the status-linkage mismatch, and exits non-zero

#### Scenario: An unmatched no-op is reported as a failure that wrote nothing

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` set and the RPC returns `was_noop: true`
  with `status_update_matched: false`
- **THEN** the command prints the "already ingested" outcome naming the `source_id`, then reports
  a failure whose message names that `source_id`, says nothing was written and that this
  workflow's run-scan row for the scan was not updated, contains neither "write-back succeeded"
  nor any claim that trait or blob data was written, and exits non-zero

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

#### Scenario: ARGO_WORKFLOW_NAME set threads through to the RPC call

- **WHEN** the command runs with the `ARGO_WORKFLOW_NAME` environment variable set (as Argo sets it
  inside the write-back container), ingesting a valid envelope
- **THEN** the RPC call includes `p_argo_workflow_name` equal to that value, whitespace-stripped

#### Scenario: A whitespace-padded ARGO_WORKFLOW_NAME is sent stripped

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` set to `" wf-a\n"`
- **THEN** the RPC call includes `p_argo_workflow_name` equal to `"wf-a"`

#### Scenario: A blank ARGO_WORKFLOW_NAME omits the parameter

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` set to `"   "`
- **THEN** the RPC call omits `p_argo_workflow_name` entirely, as when the variable is unset

#### Scenario: ARGO_WORKFLOW_NAME unset omits the parameter, unchanged from prior behavior

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` unset (the existing manual/local invocation
  shape, e.g. a human running `cyl ingest-result` directly against a scan)
- **THEN** the RPC call omits `p_argo_workflow_name` entirely, and ingestion behaves exactly as it
  did before this parameter existed

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
error, exiting zero, except in the one case "Cyl ingest command reads an envelope from a path or
stdin" reports as a failure (`ARGO_WORKFLOW_NAME` set and `status_update_matched: false`).
Re-ingesting the same envelope MUST NOT otherwise be reported as a failure.
This SHALL hold end to end, not only for the RPC's response: a re-delivery whose producer
regenerated its artifacts MUST NOT fail at the blob-upload step before the RPC's gate is reached,
and it MUST NOT be reported as a failure on account of the RPC's `status_update_matched` field
whenever the RPC matched this workflow's run-scan row, whichever `ARGO_WORKFLOW_NAME` re-delivers
it and however the source was first written (a Bloom-dispatched run, a hand-submitted Workflow, or
a manual `cyl ingest-result`) — a fresh pipeline run re-dispatching an already-ingested scan under
a **new** workflow name is exactly as benign a no-op as one re-dispatched under the same workflow
name, and the `cyl-trait-writeback` capability's fallback update, which resolves the scan from the
source's own recorded scan, is what makes that true at the RPC layer. A no-op for which the RPC
still returns `status_update_matched: false` is reported as described in "Cyl ingest command
reads an envelope from a path or stdin".

#### Scenario: First ingest of an envelope

- **WHEN** the RPC returns `was_noop=false`
- **THEN** the command prints a summary indicating the envelope was ingested (including the
  `source_id`) and exits zero

#### Scenario: Re-ingest of the same envelope

- **WHEN** the RPC returns `was_noop=true` (with a null `scan_id`, per `cyl-trait-writeback`) and
  a `status_update_matched` that is not `false`
- **THEN** the command prints an "already ingested" message (naming the `source_id`) that is
  visibly not an error, does not depend on `scan_id` being present, and exits zero

#### Scenario: Re-delivery whose producer recomputed its artifacts

- **WHEN** an already-ingested envelope is re-delivered with `--predictions-dir` containing
  `.slp` bytes that differ from those already stored at the derived path
- **THEN** the command still reports a benign, distinctly-reported no-op and exits zero, because
  the upload is skipped before the divergence can be observed

#### Scenario: Re-delivery under a new ARGO_WORKFLOW_NAME is reported as a benign no-op

- **WHEN** an envelope is first delivered successfully under one `ARGO_WORKFLOW_NAME`, the same
  scan is later re-dispatched under a **different** `ARGO_WORKFLOW_NAME` (a fresh pipeline run
  over an already-ingested scan), and the same envelope is re-delivered with that new workflow
  name threaded through to the RPC
- **THEN** the RPC's `cyl-trait-writeback` fallback marks the new workflow's
  `cyl_pipeline_run_scans` row `'written'` and returns `status_update_matched: true`, so the
  command (and the shared per-envelope batch helper `ingest_one_envelope`, used by `cyl
  batch-ingest-result`) reports the delivery as a benign, distinctly-reported no-op and exits
  zero — not a failure, and not counted against the pipeline run's `failed_count`

#### Scenario: Re-delivery of a source first written outside any Bloom run is a benign no-op

- **WHEN** an envelope was first ingested by a manual `cyl ingest-result` (no `ARGO_WORKFLOW_NAME`)
  or by a hand-submitted Workflow with no `cyl_pipeline_run_scans` rows, and a Bloom-dispatched run
  later re-delivers it with its own `ARGO_WORKFLOW_NAME`, whose `'queued'` row is for that scan
- **THEN** the RPC's fallback marks that row `'written'` and returns `status_update_matched: true`,
  and the command (and `ingest_one_envelope`) reports the delivery as a benign, distinctly-reported
  no-op and exits zero

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

<!-- 2026-09-21: this requirement's text was RAISED to match the current live spec, which already
carries it. `fix-cyl-redelivery-status-fallback` archived first (bloom#875/#880) and its delta was
written as a strict superset of this one's — adding the `status_update_matched` clause and a fourth
scenario, "Re-delivery under a new ARGO_WORKFLOW_NAME is reported as a benign no-op". This block
previously held the pre-#880 text (3 scenarios, no status_update_matched clause), so archiving this
change as-is would have replaced the live block wholesale and silently dropped both.
2026-10-01: RAISED again, to the byte-identical text of `fix-cyl-noop-redelivery-scan-resolution`'s
MODIFIED block (bloom#900), which narrows the status_update_matched clause. Until that change
deploys, this block describes behaviour that is not live yet, so this change must not archive
before it (tasks.md 9.10). -->

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
  contracts is tracked as bloom#875 (see `design.md` Risks)
  <!-- RESOLVED — see fix-cyl-redelivery-status-fallback (bloom#875, PR #880, migration
  20260917140000, live on staging 2026-09-18). "currently always does" is no longer true: the RPC
  now falls back to a scan_id-keyed UPDATE when the source_id join matches nothing, so a
  cross-workflow re-delivery reports status_update_matched=true. The normative text above is left
  as written deliberately — it belongs to this change, not that one, and rewriting another
  unarchived change's delta blind is the archive-ordering hazard that change's design.md warns
  against. Supersede it properly when this change is next revisited or archived. -->

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

