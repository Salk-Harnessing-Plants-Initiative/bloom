## ADDED Requirements

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

#### Scenario: A malformed manifest fails loud before any file is ingested

- **WHEN** `envelopes_dir` contains a `run_manifest.json` that is not valid JSON, or does not
  conform to the `RunManifest` schema
- **THEN** the command exits non-zero with a readable error before ingesting any envelope, and
  makes no RPC calls

### Requirement: A manifest-declared scan_key with no matching file is a reported batch failure

The command SHALL record a manifest-declared scan_key with no corresponding
`{scan_key}.result.json` file in `envelopes_dir` as `failed` in the batch result, with an error
message naming the missing scan_key, and SHALL count it toward the batch's non-zero exit code —
without requiring an authenticated client if no other envelope in the batch needs one.

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

## MODIFIED Requirements

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
