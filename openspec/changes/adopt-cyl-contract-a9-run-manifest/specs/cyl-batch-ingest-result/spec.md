## MODIFIED Requirements

### Requirement: Batch ingest-result command ingests every envelope in a directory

The `bloomctl` CLI SHALL provide a `cyl batch-ingest-result <envelopes_dir>` command that
discovers `{scan_key}.result.json` files directly under `envelopes_dir` (non-recursive — matching
the flat layout `trait_extractor.extractor.extract_batch`'s `output_dir` produces) and ingests each
one via the same validation + RPC path `cyl ingest-result` uses for a single envelope, including
threading `ARGO_WORKFLOW_NAME` into `p_argo_workflow_name` on every call (per the `cyl-ingest-cli`
capability). The command SHALL accept `--profile`/`-p` like the existing single-envelope command.

Every file directly under `envelopes_dir` SHALL be discovered only when no run identity is set and
no `run_manifest.json` is present. Otherwise discovery SHALL be scoped, or SHALL fail, per the
"Discovery is scoped to a present RunManifest" requirement.

After every discovered envelope has been processed (ingested, skipped, or reported failed), and only
when `ARGO_WORKFLOW_NAME` is set and non-empty, the command SHALL call
`fail_cyl_pipeline_run_scans_without_result` (capability `cyl-trait-writeback`) exactly once,
passing that environment variable and a fixed, descriptive `p_error_message`. That call closes out,
as `'failed'`, any `cyl_pipeline_run_scans` row for this workflow name that no envelope in this
batch resolved. That includes a scan whose prediction failed before producing any file at all,
which this command cannot discover directly, since it can only see files that exist. The same single
call SHALL also be made when discovery fails only because the run's manifest is missing. It SHALL NOT
be made when discovery stops on a manifest that is malformed, unreadable, a non-file entry, or names
a different run, or on a run identity the contract rejects: those fail before any client is created.
When `ARGO_WORKFLOW_NAME` is unset, the command SHALL make no such call, leaving manual/local batch
runs unaffected.

A single envelope's failure at any stage (read, validate, blob construction/upload, or the RPC call
itself) SHALL be isolated into that envelope's own failed `ScanResult`. It SHALL never abort the rest
of the batch or prevent the end-of-batch reconciliation call from running. A failure of the
reconciliation call itself SHALL likewise be isolated: it is reported as a synthetic failed
`ScanResult` (rather than raised), so the batch's own summary/`--json` output and exit code still
reflect it. On success, the number of scans the call closed out SHALL be logged.

A discovery failure that raises before any batch result exists (a missing or non-directory
`envelopes_dir`, or a manifest failure other than a missing manifest, per the "Discovery is scoped
to a present RunManifest" requirement) SHALL exit `1`.

Once a batch result exists, the exit code SHALL be non-zero exactly when at least one failed entry
is `retriable: true`. "Failed entry" covers every kind: an envelope, a missing scan_key, a missing
manifest, or the reconciliation call. Per the `cyl-ingest-cli` capability's `status_update_matched`
handling, a delivery that genuinely wrote its data but whose per-scan status linkage was skipped by
an already-permanent guard is reported as failed but marked `retriable: false`. A batch whose only
failures are all non-retriable SHALL still print/emit them as failed in the summary/`--json` output,
so the real outcome is never hidden, but SHALL exit zero.

#### Scenario: Every envelope file in the directory is ingested

- **WHEN** the user runs `bloomctl cyl batch-ingest-result /tmp/results` with `ARGO_WORKFLOW_NAME`
  unset, where `/tmp/results/` contains `scan_1.result.json`, `scan_2.result.json`,
  `scan_3.result.json`, all valid, and no `run_manifest.json` is present
- **THEN** each envelope is validated and ingested via `insert_cyl_result_envelope`, identically
  to three separate `cyl ingest-result` invocations

#### Scenario: Only top-level *.result.json files are discovered

- **WHEN** `envelopes_dir` contains `scan_1.result.json` at its top level and an unrelated
  `subdir/scan_2.result.json` nested one level down
- **THEN** only `scan_1.result.json` is discovered and ingested; the nested file is not

#### Scenario: ARGO_WORKFLOW_NAME set reconciles unresolved scans after the batch

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` set to `"wf-a"`, `run_manifest.wf-a.json`
  resolves, and one scan dispatched under that workflow name has no corresponding
  `{scan_key}.result.json` file anywhere in `envelopes_dir` (its prediction never produced a result)
- **THEN** after every discovered envelope is processed, the command calls
  `fail_cyl_pipeline_run_scans_without_result` once with `"wf-a"`, which marks that scan's
  `cyl_pipeline_run_scans` row `'failed'`

#### Scenario: ARGO_WORKFLOW_NAME unset makes no reconciliation call

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` unset (a manual/local batch run with no
  pipeline-run context)
- **THEN** the command never calls `fail_cyl_pipeline_run_scans_without_result`, regardless of
  whether any envelope failed or was skipped

#### Scenario: The reconciliation call happens exactly once regardless of batch size

- **WHEN** `ARGO_WORKFLOW_NAME` is set, the run's manifest resolves, and the batch contains any
  number of envelopes (including zero, when none of the manifest's declared files are present)
- **THEN** `fail_cyl_pipeline_run_scans_without_result` is called exactly once, after all envelopes
  (if any) have been processed — never once per envelope

#### Scenario: An unreadable envelope file does not abort the batch or skip reconciliation

- **WHEN** one envelope file in the batch cannot be read as UTF-8 text (e.g. truncated mid-write by
  an OOM-killed producer), and `ARGO_WORKFLOW_NAME` is set
- **THEN** that envelope is reported as a failed `ScanResult`, every other envelope in the batch is
  still ingested normally, and the end-of-batch reconciliation call still runs

#### Scenario: A reconciliation-call failure is isolated, not a crash

- **WHEN** every envelope in the batch ingests successfully but the end-of-batch
  `fail_cyl_pipeline_run_scans_without_result` call itself raises (e.g. a transient network/auth
  error)
- **THEN** the command does not crash with an unhandled exception; it still prints the batch summary
  (or `--json` output) reflecting every envelope's real outcome, includes a distinct failed entry
  describing the reconciliation failure, and exits non-zero

#### Scenario: A successful reconciliation logs how many scans it closed out

- **WHEN** the end-of-batch reconciliation call succeeds and closes out one or more scans as
  `'failed'`
- **THEN** the number of scans closed out is logged, rather than discarded silently

#### Scenario: A batch whose only failure is non-retriable exits zero

- **WHEN** every failed `ScanResult` in the batch has `retriable: false` (e.g. a delivery whose
  data was written correctly but whose `status_update_matched` came back `false`), and none has
  `retriable: true`
- **THEN** the batch summary/`--json` output still reports those entries as `"failed"` with their
  real error messages, but the command exits zero

#### Scenario: A batch mixing a retriable and a non-retriable failure exits non-zero

- **WHEN** the batch has at least one failed `ScanResult` with `retriable: true` (e.g. a genuine
  RPC or network error) alongside one or more with `retriable: false`
- **THEN** the command exits non-zero — the retriable failure alone is enough to warrant a retry,
  even though the retry cannot change the non-retriable one's outcome

### Requirement: One envelope's failure is isolated, not fatal to the batch

The command SHALL ingest every envelope independently: an envelope that fails (the file is not
readable or not valid JSON, schema validation, a mapped RPC error, or an unrecognized RPC error)
SHALL be recorded as `failed` with a per-envelope error message, and SHALL NOT prevent the
remaining envelopes in the batch from being ingested. The exit code SHALL follow the exit-code rule
of the "Batch ingest-result command ingests every envelope in a directory" requirement: zero when no
failed entry is retriable. That covers a batch where every envelope succeeded, was a no-op
re-delivery, or failed non-retriably, and where no missing-scan_key, missing-manifest or
reconciliation entry failed, including a directory with no envelope files when no manifest was
resolved or required. A resolved manifest declaring a scan_key with no matching
file is a batch failure, not a no-op, per the "A manifest-declared scan_key with no matching file is
a reported batch failure" requirement, and its entries are `retriable: true`. A run identity with
no resolvable manifest is a failure per the "Discovery is scoped to a present RunManifest"
requirement.

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

- **WHEN** `ARGO_WORKFLOW_NAME` is unset, and `envelopes_dir` exists but contains no
  `*.result.json` files and no `run_manifest.json`
- **THEN** the command makes no RPC calls, reports zero envelopes, and exits zero

### Requirement: Discovery is scoped to a present RunManifest

The command SHALL resolve its run manifest with
`sleap_roots_contracts.load_run_manifest(envelopes_dir, pipeline_run_id_from_env(),
allow_legacy=True)`, and never by building a manifest path itself. Here the *run identity* is the
whitespace-stripped value `pipeline_run_id_from_env()` returns, which is `None` when
`ARGO_WORKFLOW_NAME` is unset or blank. The file resolved is the one
`trait_extractor.extractor.extract_batch` forwards into its `output_dir`, which is this command's
`envelopes_dir`.

When a manifest resolves, the command SHALL restrict discovery to only the `{scan_key}.result.json`
files whose filename stem is listed in that manifest's `scan_keys`. A `.result.json` file present in
`envelopes_dir` but not listed SHALL be excluded from the batch entirely (not ingested, not reported
as a failure) and SHALL be logged at debug level.

With a run identity, the command SHALL use `run_manifest.<id>.json`, and SHALL fall back to the
legacy `run_manifest.json` only when the per-run file is absent. If neither exists, the command
SHALL NOT fall back to unscoped discovery. Instead it SHALL ingest no envelope, make the single
reconciliation call described in the "Batch ingest-result command ingests every envelope in a
directory" requirement (isolated as it describes), report a failed, retriable batch entry whose
`scan_key` is the sentinel `"<run-manifest>"` and whose message names both file names it looked
for, and exit non-zero. It exits non-zero so that the write-back step, and with it the batch's
Workflow, ends `Failed` rather than succeeding with nothing ingested. An automated retry of the step
repeats the same outcome; the reconciliation call it repeats is idempotent.

When the per-run file names a different `pipeline_run_id` than the run identity, or when the
contract rejects the run identity itself, the command SHALL fail before ingesting any envelope,
without authenticating or reconciling. When a legacy fallback names a different `pipeline_run_id`
than the run identity, the command SHALL scope to it and log a warning naming both ids.

Without a run identity, only `run_manifest.json` SHALL be consulted, and when it is absent,
discovery SHALL be fully unscoped.

The run identity is used only to resolve the manifest. `p_argo_workflow_name` on the insert and
reconciliation calls SHALL remain the raw `ARGO_WORKFLOW_NAME` value, per the "Batch ingest-result
command ingests every envelope in a directory" requirement. So a blank value (`"   "`) resolves no
run identity, which gives unscoped discovery, and still triggers the single reconciliation call
under that raw value.

#### Scenario: The run's per-run manifest scopes discovery to its scan_keys

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, and `envelopes_dir` contains `scan_1.result.json`,
  `scan_2.result.json`, and `run_manifest.wf-a.json` whose `scan_keys` is `["scan_1"]`
- **THEN** only `scan_1.result.json` is ingested; `scan_2.result.json` is excluded from the batch
  and not reported as a failure

#### Scenario: The per-run manifest wins over a stale legacy manifest

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, `run_manifest.wf-a.json` lists `["scan_1"]`, and a
  stale `run_manifest.json` lists `["scan_1", "scan_2"]`
- **THEN** discovery is scoped to `["scan_1"]` only

#### Scenario: Another run's per-run manifest is ignored

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, and `envelopes_dir` holds `run_manifest.wf-b.json` and
  `run_manifest.wf-a.json`, which list different scan_keys
- **THEN** discovery is scoped to `run_manifest.wf-a.json`'s `scan_keys` only

#### Scenario: A legacy fallback naming another run is used with a warning

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, no `run_manifest.wf-a.json` exists, and
  `run_manifest.json` has `pipeline_run_id` `"wf-old"` and `scan_keys` `["scan_1"]`
- **THEN** discovery is scoped to `["scan_1"]`, and a warning-level log line names both `"wf-a"`
  and `"wf-old"`

#### Scenario: A legacy fallback naming the same run is used without a warning

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, no `run_manifest.wf-a.json` exists, and
  `run_manifest.json` has `pipeline_run_id` `"wf-a"`
- **THEN** discovery is scoped to that file's `scan_keys`, and no warning-level log line is emitted

#### Scenario: A run identity with no manifest fails loud and still reconciles

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, and `envelopes_dir` contains `scan_1.result.json` but
  neither `run_manifest.wf-a.json` nor `run_manifest.json`
- **THEN** `scan_1.result.json` is not ingested (no `insert_cyl_result_envelope` call), the command
  calls `fail_cyl_pipeline_run_scans_without_result` once with `"wf-a"`, reports a failed batch
  entry naming `run_manifest.wf-a.json` and `run_manifest.json`, and exits non-zero

#### Scenario: A reconciliation failure on the missing-manifest path is isolated

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, no manifest resolves, and the reconciliation call
  raises
- **THEN** the command does not crash; its summary/`--json` output reports both the missing-manifest
  entry and the reconciliation failure, and it exits non-zero

#### Scenario: A per-run manifest naming a different run fails loud

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, and `run_manifest.wf-a.json`'s `pipeline_run_id` is
  `"wf-b"`
- **THEN** the command exits non-zero with a readable error before ingesting any envelope, and
  never authenticates, reconciles, or makes any RPC call

#### Scenario: A run identity the contract rejects fails loud

- **WHEN** `ARGO_WORKFLOW_NAME` is `"../wf"`
- **THEN** the command exits non-zero with a readable error naming the value, and never
  authenticates, reconciles, or makes any RPC call

#### Scenario: An excluded out-of-scope file is logged at debug level

- **WHEN** a `.result.json` file present in `envelopes_dir` is excluded because its scan_key is not
  in the manifest's `scan_keys`
- **THEN** a debug-level log line names the excluded scan_key and the manifest file that scoped it

#### Scenario: No run identity and no manifest means fully unscoped discovery

- **WHEN** `ARGO_WORKFLOW_NAME` is unset or blank and `envelopes_dir` contains no
  `run_manifest.json`
- **THEN** every `{scan_key}.result.json` file directly under `envelopes_dir` is discovered and
  ingested, even when per-run manifests such as `run_manifest.wf-a.json` are present (covers
  manual/dev CLI use with no manifest)

#### Scenario: A blank ARGO_WORKFLOW_NAME is unscoped but still reconciles once

- **WHEN** `ARGO_WORKFLOW_NAME` is `"   "`, `envelopes_dir` holds `scan_1.result.json` and
  `scan_2.result.json`, and no `run_manifest.json` is present
- **THEN** both envelopes are ingested, `fail_cyl_pipeline_run_scans_without_result` is called
  exactly once with `"   "`, and the same single call is made when `envelopes_dir` is empty

#### Scenario: No run identity scopes to a present legacy manifest

- **WHEN** `ARGO_WORKFLOW_NAME` is unset and `run_manifest.json` lists `["scan_1"]` while
  `envelopes_dir` holds `scan_1.result.json` and `scan_2.result.json`
- **THEN** only `scan_1.result.json` is ingested

#### Scenario: A malformed or unreadable manifest fails loud before any file is ingested

- **WHEN** the manifest the command resolves is not valid JSON, does not conform to the
  `RunManifest` schema, or cannot be read (e.g. a permission error)
- **THEN** the command exits non-zero with a readable error before ingesting any envelope, and
  never authenticates, reconciles, or makes any RPC call

#### Scenario: A manifest path occupied by a non-file entry fails loud

- **WHEN** the manifest path the command would read exists but is a directory, a dangling symlink,
  or another non-file entry, not a regular file
- **THEN** the command exits non-zero with a readable error before ingesting any envelope, and
  never authenticates, reconciles, or makes any RPC call — it never falls through to the next
  candidate name

### Requirement: A manifest-declared scan_key with no matching file is a reported batch failure

The command SHALL record a manifest-declared scan_key with no corresponding
`{scan_key}.result.json` file in `envelopes_dir` as `failed` in the batch result, with an error
message naming the missing scan_key and the manifest file that declared it, and SHALL count it
toward the batch's non-zero exit code. It SHALL not require an authenticated client if no other
envelope in the batch needs one and `ARGO_WORKFLOW_NAME` is unset. When that variable is set, the
client is needed only for the end-of-batch reconciliation call. If an ingested envelope's own
content-derived scan_key (which can differ from its file's name) coincides with a manifest-declared
scan_key that had no identically-named file, the command SHALL NOT report that scan_key as both a
failure (via this requirement) and a successful or skipped outcome in the same batch result. The
actual ingest outcome SHALL take precedence, and the failure entry for that scan_key SHALL be
omitted.

#### Scenario: A missing manifest-declared scan_key is reported failed

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, the resolved manifest `run_manifest.wf-a.json`'s
  `scan_keys` includes `scan_9`, and no `scan_9.result.json` exists in `envelopes_dir`
- **THEN** the batch result reports `scan_9` as `failed` with a message naming it as missing and
  naming `run_manifest.wf-a.json`, and the command exits non-zero

#### Scenario: A missing scan_key is reported without ever authenticating

- **WHEN** `ARGO_WORKFLOW_NAME` is unset, `envelopes_dir` contains a `run_manifest.json` whose every
  declared scan_key is missing, and no other `.result.json` files are present
- **THEN** the command reports every declared scan_key as `failed` and exits non-zero, without ever
  calling `_authed_client` or making any RPC call — this is distinct from the "empty envelopes
  directory is a no-op" scenario, which applies only when there is no manifest and no files at all

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
