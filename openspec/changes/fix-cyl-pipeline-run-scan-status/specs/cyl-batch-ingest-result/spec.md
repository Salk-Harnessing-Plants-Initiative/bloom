## MODIFIED Requirements

### Requirement: Batch ingest-result command ingests every envelope in a directory

The `bloomctl` CLI SHALL provide a `cyl batch-ingest-result <envelopes_dir>` command that
discovers every `{scan_key}.result.json` file directly under `envelopes_dir` (non-recursive —
matching the flat layout `trait_extractor.extractor.extract_batch`'s `output_dir`
produces) and ingests each one via the same validation + RPC path `cyl ingest-result` uses for a
single envelope, including threading `ARGO_WORKFLOW_NAME` into `p_argo_workflow_name` on every call
(per the `cyl-ingest-cli` capability). The command SHALL accept `--profile`/`-p` like the existing
single-envelope command. This unconditional "every file" behavior applies only when `envelopes_dir`
contains no `run_manifest.json` — when one is present, discovery SHALL instead be scoped per the
"Discovery is scoped to a present RunManifest" requirement above. After every discovered envelope
has been processed (ingested, skipped, or reported failed), and only when `ARGO_WORKFLOW_NAME` is
set and non-empty, the command SHALL call `fail_cyl_pipeline_run_scans_without_result` (capability
`cyl-trait-writeback`) exactly once, passing that environment variable and a fixed, descriptive
`p_error_message` — closing out, as `'failed'`, any `cyl_pipeline_run_scans` row for this workflow
name that no envelope in this batch ever resolved (including a scan whose prediction failed before
producing any file at all, which this command has no way to discover directly since it can only see
files that exist). When `ARGO_WORKFLOW_NAME` is unset, the command SHALL make no such call, leaving
manual/local batch runs unaffected.

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

#### Scenario: ARGO_WORKFLOW_NAME set reconciles unresolved scans after the batch

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` set, and one scan dispatched under that
  workflow name has no corresponding `{scan_key}.result.json` file anywhere in `envelopes_dir` (its
  prediction never produced a result)
- **THEN** after every discovered envelope is processed, the command calls
  `fail_cyl_pipeline_run_scans_without_result` once with that `ARGO_WORKFLOW_NAME`, which marks that
  scan's `cyl_pipeline_run_scans` row `'failed'`

#### Scenario: ARGO_WORKFLOW_NAME unset makes no reconciliation call

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` unset (a manual/local batch run with no
  pipeline-run context)
- **THEN** the command never calls `fail_cyl_pipeline_run_scans_without_result`, regardless of
  whether any envelope failed or was skipped

#### Scenario: The reconciliation call happens exactly once regardless of batch size

- **WHEN** `ARGO_WORKFLOW_NAME` is set and the batch contains any number of envelopes (including
  zero, if `envelopes_dir` is empty and no manifest is present)
- **THEN** `fail_cyl_pipeline_run_scans_without_result` is called exactly once, after all envelopes
  (if any) have been processed — never once per envelope
