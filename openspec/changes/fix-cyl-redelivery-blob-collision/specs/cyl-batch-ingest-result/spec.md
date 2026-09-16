## MODIFIED Requirements

### Requirement: Optional --predictions-dir constructs and uploads blobs per envelope

The command SHALL accept an optional `--predictions-dir` option pointing at predict's nested
batch output root. When given, for each envelope the command SHALL first check whether
`cyl_trait_sources` already holds that envelope's `provenance.idempotency_key`; if it does, the
command SHALL skip blob handling for that envelope entirely — no manifest lookup, no checksum
verification, no upload — and proceed directly to the RPC call, which reports the delivery as a
no-op (see `cyl-ingest-cli`'s "An already-ingested envelope skips blob upload entirely"). The
check SHALL fail open: an error reading `cyl_trait_sources` is treated as "not already ingested"
and MUST NOT be recorded as that envelope's failure.

Otherwise the command SHALL look up
`predictions_dir/{scan_key}/{scan_key}.predictions.json` (the envelope's own `scan_key`) and, if
present, construct + verify + upload its blobs via the same `load_predictions_manifest`/
`build_pending_blobs`/`upload_pending_blobs` helpers `cyl ingest-result --predictions-dir` uses,
merging the resulting blobs into that envelope before the RPC call. A missing manifest or a blob
upload failure for one envelope SHALL be recorded as that envelope's failure (no RPC call for
it) and SHALL NOT prevent other envelopes in the batch from being processed.

#### Scenario: Blobs are uploaded per-scan from predict's nested output

- **WHEN** `--predictions-dir /predict-out` is given and `/predict-out/scan_1/` contains a valid
  `scan_1.predictions.json` + `.slp` files
- **THEN** `scan_1`'s envelope is ingested with its blobs constructed, verified, and uploaded,
  matching what a single `cyl ingest-result --predictions-dir /predict-out/scan_1` call would
  produce

#### Scenario: A missing manifest for one scan isolates that scan's failure

- **WHEN** `--predictions-dir` is given but one envelope's scan_key has no corresponding
  `{scan_key}.predictions.json` under it
- **THEN** that envelope is reported `failed` with a message naming the missing manifest, no RPC
  call is made for it, and the other envelopes in the batch are still processed normally

#### Scenario: An already-ingested envelope in the batch skips its blob work

- **WHEN** one envelope in the batch has an `idempotency_key` already present in
  `cyl_trait_sources`, and its `.slp` files on disk differ byte-wise from those already stored
- **THEN** that envelope's manifest is never read and no upload is attempted, it is reported
  `skipped` via the RPC's `was_noop=true`, the batch does not count it as a failure, and the
  other envelopes are processed normally

#### Scenario: A missing manifest for an already-ingested scan is not a failure

- **WHEN** an envelope's `idempotency_key` is already present in `cyl_trait_sources` and its
  `{scan_key}.predictions.json` is absent from `--predictions-dir`
- **THEN** the envelope is reported `skipped`, not `failed`, because the skip precedes the
  manifest lookup entirely
