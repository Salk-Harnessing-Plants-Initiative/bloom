## MODIFIED Requirements

### Requirement: Optional --predictions-dir constructs and uploads blobs per envelope

The command SHALL accept an optional `--predictions-dir` option pointing at predict's nested
batch output root. When given, for each envelope the command SHALL look up
`predictions_dir/{scan_key}/{scan_key}.predictions.json` (the envelope's own `scan_key`) and, if
present, construct + verify + upload its blobs via the same `load_predictions_manifest`/
`build_pending_blobs`/`upload_pending_blobs` helpers `cyl ingest-result --predictions-dir` uses,
merging the resulting blobs into that envelope before the RPC call — unless that envelope's
`idempotency_key` is already present in `cyl_trait_sources`, in which case the upload and the
merge are both skipped (see below). A missing manifest or a blob
upload failure for one envelope SHALL be recorded as that envelope's failure (no RPC call for
it) and SHALL NOT prevent other envelopes in the batch from being processed.

After constructing that envelope's blobs and before uploading them, the command SHALL check
whether `cyl_trait_sources` already holds that envelope's `provenance.idempotency_key`; if it
does, the command SHALL skip the upload and the blobs merge for that envelope and proceed to the
RPC call, which reports the delivery as a no-op (see `cyl-ingest-cli`'s "An already-ingested
envelope skips blob upload"). Because the check follows manifest loading and blob construction,
a missing manifest remains that envelope's failure whether or not it was already ingested. The
check SHALL fail open: an error reading `cyl_trait_sources` is treated as "not already ingested",
MUST NOT be recorded as that envelope's failure, and SHALL be surfaced as a warning on that
envelope's reported result rather than only in a log.

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

#### Scenario: An already-ingested envelope in the batch skips its upload

- **WHEN** one envelope in the batch has an `idempotency_key` already present in
  `cyl_trait_sources`, and its `.slp` files on disk differ byte-wise from those already stored
- **THEN** that envelope's blobs are constructed but not uploaded and not merged, it is reported
  `skipped` via the RPC's `was_noop=true`, the batch does not count it as a failure, and the
  other envelopes are processed normally

#### Scenario: One envelope's check failing does not fail that envelope

- **WHEN** the `cyl_trait_sources` lookup raises for one envelope in the batch while succeeding
  for the others
- **THEN** that envelope falls through to construct-and-upload as it would without the check and
  is reported on its own merits, its result carries a warning naming the degraded check, and the
  remaining envelopes are unaffected
