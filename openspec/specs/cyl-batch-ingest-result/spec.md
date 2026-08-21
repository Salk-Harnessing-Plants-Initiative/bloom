# cyl-batch-ingest-result Specification

## Purpose
TBD - created by archiving change add-cyl-batch-commands. Update Purpose after archive.
## Requirements
### Requirement: Batch ingest-result command ingests every envelope in a directory

The `bloomctl` CLI SHALL provide a `cyl batch-ingest-result <envelopes_dir>` command that
discovers every `{scan_key}.result.json` file directly under `envelopes_dir` (non-recursive —
matching the flat layout `trait_extractor.extractor.extract_batch`'s `output_dir`
produces) and ingests each one via the same validation + RPC path `cyl ingest-result` uses for a
single envelope. The command SHALL accept `--profile`/`-p` like the existing single-envelope
command. This unconditional "every file" behavior applies only when `envelopes_dir` contains no
`run_manifest.json` — when one is present, discovery SHALL instead be scoped per the "Discovery is
scoped to a present RunManifest" requirement above.

#### Scenario: Every envelope file in the directory is ingested

- **WHEN** the user runs `bloomctl cyl batch-ingest-result /tmp/results` where `/tmp/results/`
  contains `scan_1.result.json`, `scan_2.result.json`, `scan_3.result.json`, all valid, and no
  `run_manifest.json` is present
- **THEN** each envelope is validated and ingested via `insert_cyl_result_envelope`, identically
  to three separate `cyl ingest-result` invocations

#### Scenario: Only top-level *.result.json files are discovered

- **WHEN** `envelopes_dir` contains `scan_1.result.json` at its top level and an unrelated
  `subdir/scan_2.result.json` nested one level down
- **THEN** only `scan_1.result.json` is discovered and ingested; the nested file is not

### Requirement: A nonexistent or non-directory envelopes_dir is rejected before any I/O

The command SHALL exit non-zero with a readable error, and SHALL make no RPC calls, if
`envelopes_dir` does not exist or is not a directory (e.g. a file path was given instead).

#### Scenario: envelopes_dir does not exist or is not a directory

- **WHEN** `envelopes_dir` is a path that does not exist, or is a file rather than a directory
- **THEN** the command exits non-zero with a readable error and makes no RPC calls

### Requirement: One envelope's failure is isolated, not fatal to the batch

The command SHALL ingest every envelope independently: an envelope that fails (the file is not
readable or not valid JSON, schema validation, a mapped RPC error, or an unrecognized RPC error)
SHALL be recorded as `failed` with a per-envelope error message, and SHALL NOT prevent the
remaining envelopes in the batch from being ingested. The command SHALL exit non-zero if any
envelope in the batch failed, and SHALL exit zero if every envelope succeeded, was a no-op
re-delivery, or the directory contained no envelope files and no `run_manifest.json` declaring any
scan_key — a present manifest declaring a scan_key with no matching file is a batch failure, not a
no-op, per the "A manifest-declared scan_key with no matching file is a reported batch failure"
requirement above.

#### Scenario: One bad envelope among several does not abort the batch

- **WHEN** a batch of 3 envelope files includes one that fails `sleap-roots-contracts` validation
- **THEN** the other 2 envelopes are ingested successfully via the RPC, the bad envelope is
  reported `failed` by its `scan_key` with its validation error, and the command exits non-zero

#### Scenario: A malformed envelope file is isolated, not fatal to the batch

- **WHEN** a batch of 3 envelope files includes one whose content is not valid JSON (e.g. a
  truncated or corrupted `.result.json`)
- **THEN** the other 2 envelopes are ingested successfully via the RPC, the malformed file is
  reported `failed` (named by its filename, since no `scan_key` could be read from it), and the
  command exits non-zero

#### Scenario: Empty envelopes directory is a no-op, not an error

- **WHEN** `envelopes_dir` exists but contains no `*.result.json` files, and no `run_manifest.json`
  is present
- **THEN** the command makes no RPC calls, reports zero envelopes, and exits zero

### Requirement: A no-op re-delivery is reported as skipped, not failed

The command SHALL report an envelope for which the RPC returns `was_noop=true` (an
already-ingested, first-writer-wins re-delivery) with `status="skipped"` — distinct from both
`ok` and `failed` — and SHALL NOT count it toward the batch's failure exit code.

#### Scenario: Re-ingesting an already-ingested envelope in a batch

- **WHEN** one of the envelopes in the batch was already ingested in a prior run
- **THEN** that envelope is reported `skipped` (not `failed`), and the batch still exits zero if
  every other envelope succeeded or was also skipped

### Requirement: Optional --predictions-dir constructs and uploads blobs per envelope

The command SHALL accept an optional `--predictions-dir` option pointing at predict's nested
batch output root. When given, for each envelope the command SHALL look up
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

### Requirement: Machine-readable batch result output

The command SHALL support a `--json` flag that writes the aggregate batch result to stdout as
JSON — one entry per envelope with its `scan_key`, `status` (`ok`/`skipped`/`failed`), and `error`
(empty unless `failed`). Without the flag, the command SHALL print a human-readable summary line
(count ingested / skipped / failed) plus one line per failed envelope naming it and its error.

#### Scenario: JSON output enumerates every envelope's status

- **WHEN** the user passes `--json` on a batch with a mix of ok, skipped, and failed envelopes
- **THEN** stdout contains a parseable JSON array with one entry per envelope, each carrying its
  `scan_key` and `status`, and `error` populated only for `failed` entries

#### Scenario: Default human-readable output names every failure

- **WHEN** the user omits `--json` and one envelope in the batch failed
- **THEN** stdout contains a summary count and a line identifying the failed envelope by its
  `scan_key` and error message

### Requirement: Discovery is scoped to a present RunManifest

The command SHALL restrict discovery to only the `{scan_key}.result.json` files whose filename
stem is listed in a present manifest's `scan_keys`, when `envelopes_dir` contains a
`run_manifest.json` (a `sleap_roots_contracts.RunManifest`, the file
`trait_extractor.extractor.extract_batch` copies forward into its `output_dir` — which is this
command's `envelopes_dir`). A `.result.json` file present in `envelopes_dir` but not listed in the
manifest SHALL be excluded from the batch entirely (not ingested, not reported as a failure) and
SHALL be logged at debug level. When no `run_manifest.json` is present, discovery SHALL be fully
unscoped — identical to the "Batch ingest-result command ingests every envelope in a directory"
requirement's existing behavior.

#### Scenario: A manifest present in envelopes_dir scopes discovery to its scan_keys

- **WHEN** `envelopes_dir` contains `scan_1.result.json` and `scan_2.result.json`, and a
  `run_manifest.json` whose `scan_keys` is `["scan_1"]`
- **THEN** only `scan_1.result.json` is ingested; `scan_2.result.json` is excluded from the batch
  and not reported as a failure

#### Scenario: An excluded out-of-scope file is logged at debug level

- **WHEN** a `.result.json` file present in `envelopes_dir` is excluded because its scan_key is not
  in the manifest's `scan_keys`
- **THEN** a debug-level log line names the excluded scan_key

#### Scenario: No manifest present means fully unscoped discovery, unchanged

- **WHEN** `envelopes_dir` contains no `run_manifest.json`
- **THEN** every `{scan_key}.result.json` file directly under `envelopes_dir` is discovered and
  ingested, exactly as if this requirement did not exist (covers both manual/dev CLI use with no
  manifest, and the case where an upstream manifest-writing stage has not yet been deployed)

#### Scenario: A malformed or unreadable manifest fails loud before any file is ingested

- **WHEN** `envelopes_dir` contains a `run_manifest.json` that is not valid JSON, does not conform
  to the `RunManifest` schema, or cannot be read (e.g. a permission error)
- **THEN** the command exits non-zero with a readable error before ingesting any envelope, and
  makes no RPC calls

#### Scenario: A run_manifest.json path occupied by a non-file entry fails loud

- **WHEN** `envelopes_dir / "run_manifest.json"` exists but is a directory (or other non-file
  entry), not a regular file
- **THEN** the command exits non-zero with a readable error before ingesting any envelope, and
  makes no RPC calls — this is distinct from the "no run_manifest.json is present" case, which
  falls back to fully unscoped discovery rather than failing

### Requirement: A manifest-declared scan_key with no matching file is a reported batch failure

The command SHALL record a manifest-declared scan_key with no corresponding
`{scan_key}.result.json` file in `envelopes_dir` as `failed` in the batch result, with an error
message naming the missing scan_key, and SHALL count it toward the batch's non-zero exit code —
without requiring an authenticated client if no other envelope in the batch needs one. If an
ingested envelope's own content-derived scan_key (which can differ from its file's name) coincides
with a manifest-declared scan_key that had no identically-named file, the command SHALL NOT report
that scan_key as both a failure (via this requirement) and a successful or skipped outcome in the
same batch result — the actual ingest outcome SHALL take precedence, and the failure entry for that
scan_key SHALL be omitted.

#### Scenario: A missing manifest-declared scan_key is reported failed

- **WHEN** a manifest's `scan_keys` includes `scan_9`, and no `scan_9.result.json` exists in
  `envelopes_dir`
- **THEN** the batch result reports `scan_9` as `failed` with a message naming it as missing, and
  the command exits non-zero

#### Scenario: A missing scan_key is reported without ever authenticating

- **WHEN** `envelopes_dir` contains a manifest whose every declared scan_key is missing, and no
  other `.result.json` files are present
- **THEN** the command reports every declared scan_key as `failed` and exits non-zero, without ever
  calling `_authed_client` or making any RPC call — this is distinct from the existing "empty
  envelopes directory is a no-op" scenario, which applies only when there is no manifest and no
  files at all

#### Scenario: A missing scan_key alongside successfully ingested envelopes

- **WHEN** a manifest lists `scan_1` (present and valid) and `scan_2` (missing), and
  `envelopes_dir` contains only `scan_1.result.json`
- **THEN** `scan_1` is ingested normally via the RPC, `scan_2` is reported `failed` as missing in
  the same batch result, and the command exits non-zero

#### Scenario: A filename/body scan_key mismatch does not produce a duplicate contradictory entry

- **WHEN** a manifest lists `scan_A` and `scan_B`, `envelopes_dir` contains only
  `scan_A.result.json`, and that file's own `provenance.scan_key` is `scan_B` (so
  `ingest_one_envelope` reports its outcome under `scan_B`, not `scan_A`)
- **THEN** the batch result contains exactly one entry for `scan_B`, reflecting its actual ingest
  outcome (e.g. `ok`) — not also a separate `failed` entry for `scan_B` from the manifest's missing-
  scan_key check

