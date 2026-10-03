## MODIFIED Requirements

### Requirement: Batch ingest-result command ingests every envelope in a directory

The `bloomctl` CLI SHALL provide a `cyl batch-ingest-result <envelopes_dir>` command that
discovers `{scan_key}.result.json` files directly under `envelopes_dir` (non-recursive — matching
the flat layout `trait_extractor.extractor.extract_batch`'s `output_dir` produces) and ingests each
one via the same validation + RPC path `cyl ingest-result` uses for a single envelope, including
threading the run identity into `p_argo_workflow_name` on every call (per the `cyl-ingest-cli`
capability). The *run identity* is the single value `sleap_roots_contracts.pipeline_run_id_from_env()`
returns: the whitespace-stripped `ARGO_WORKFLOW_NAME`, or `None` when it is unset or blank. The
command SHALL use that one value for manifest resolution, for every insert call and for the
reconciliation call, so they can never target different workflow names. The command SHALL accept `--profile`/`-p` like the existing single-envelope command.

Every file directly under `envelopes_dir` SHALL be discovered only when no run identity is set and
no `run_manifest.json` is present. Otherwise discovery SHALL be scoped, or SHALL fail, per the
"Discovery is scoped to a present RunManifest" requirement.

After every discovered envelope has been processed (ingested, skipped, or reported failed), and only
when the run identity is not `None` and no envelope file this invocation attempted to ingest is
reported failed with `retriable: true`, the command SHALL call
`fail_cyl_pipeline_run_scans_without_result` (capability `cyl-trait-writeback`) exactly once,
passing the run identity and a fixed, descriptive `p_error_message`. A missing manifest-declared
scan_key, a missing run manifest, and a non-retriable envelope failure SHALL NOT prevent the call.
When an attempted envelope did fail retriably, an automated retry of the write-back step could still
write that scan's result, so the command SHALL make no reconciliation call, SHALL add no
reconciliation entry to the batch result, and SHALL write one line to stderr saying that
reconciliation was deferred to the status poller and how many envelopes failed retriably; the status
poller (capability `cyl-pipeline-status-polling`) closes out this workflow's still-`'queued'` rows
once the workflow can write nothing more. That call closes out,
as `'failed'`, any `cyl_pipeline_run_scans` row for this workflow name that no envelope in this
batch resolved. That includes a scan whose prediction failed before producing any file at all,
which this command cannot discover directly, since it can only see files that exist. The same single
call SHALL also be made when discovery finds no manifest for this run, per the "Discovery is scoped
to a present RunManifest" requirement; its `p_error_message` then SHALL say that no run manifest
reached write-back, not that no result was produced, since the scans' envelopes may exist. It SHALL
NOT be made when discovery stops on a manifest that is malformed, unreadable, or a non-file entry, on
a per-run file naming a different run, or on a run identity the contract rejects: those fail before
any client is created. Without a run identity, the command SHALL make no such call, leaving
manual/local batch runs unaffected.

A single envelope's failure at any stage (read, validate, blob construction/upload, or the RPC call
itself) SHALL be isolated into that envelope's own failed `ScanResult`. It SHALL never abort the rest
of the batch. When that failure is retriable, it defers the end-of-batch reconciliation call as
described above; otherwise it does not affect that call. A failure of the
reconciliation call itself SHALL likewise be isolated: it is reported as a synthetic failed
`ScanResult` (rather than raised), so the batch's own summary/`--json` output and exit code still
reflect it. On success, the number of scans the call closed out SHALL be logged.

A discovery failure that raises before any batch result exists (a missing or non-directory
`envelopes_dir`, or a manifest failure other than a missing manifest, per the "Discovery is scoped
to a present RunManifest" requirement) SHALL exit `1`.

Once a batch result exists, the exit code SHALL be non-zero exactly when at least one failed entry
is `retriable: true`. "Failed entry" covers every kind: an envelope, a missing scan_key, a missing
manifest, or the reconciliation call. Per the `cyl-ingest-cli` capability's `status_update_matched`
handling, a delivery whose per-scan status linkage was not updated (`status_update_matched:
false`) — one that genuinely wrote its data, or an already-ingested no-op that wrote nothing — is
reported as failed but marked `retriable: false`. A batch whose only
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
  resolves, every envelope the batch attempts ingests (or is skipped as a no-op), and one scan
  dispatched under that workflow name has no corresponding `{scan_key}.result.json` file anywhere in
  `envelopes_dir` (its prediction never produced a result)
- **THEN** after every discovered envelope is processed, the command calls
  `fail_cyl_pipeline_run_scans_without_result` once with `"wf-a"`, which marks that scan's
  `cyl_pipeline_run_scans` row `'failed'`

#### Scenario: ARGO_WORKFLOW_NAME unset makes no reconciliation call

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` unset (a manual/local batch run with no
  pipeline-run context)
- **THEN** the command never calls `fail_cyl_pipeline_run_scans_without_result`, regardless of
  whether any envelope failed or was skipped

#### Scenario: The reconciliation call happens exactly once regardless of batch size

- **WHEN** `ARGO_WORKFLOW_NAME` is set, the run's manifest resolves, no envelope the batch attempts
  fails retriably, and the batch contains any number of envelopes (including zero, when none of the
  manifest's declared files are present)
- **THEN** `fail_cyl_pipeline_run_scans_without_result` is called exactly once, after all envelopes
  (if any) have been processed — never once per envelope

#### Scenario: An unreadable envelope file does not abort the batch

- **WHEN** one envelope file in the batch cannot be read as UTF-8 text (e.g. truncated mid-write by
  an OOM-killed producer), and `ARGO_WORKFLOW_NAME` is set
- **THEN** that envelope is reported as a failed, retriable `ScanResult`, every other envelope in the
  batch is still ingested normally, and the command exits non-zero
- **AND** because that failure is retriable, no reconciliation call is made; the status poller
  closes out any row still `'queued'` once the workflow is terminal

#### Scenario: A retriable envelope failure defers reconciliation to the status poller

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, `run_manifest.wf-a.json` lists `scan_1` and `scan_2`,
  `scan_1` ingests, `scan_2`'s ingest fails with `retriable: true` (e.g. a transient RPC or network
  error), and a third scan dispatched under `"wf-a"` is not listed in the manifest
- **THEN** the command makes no `fail_cyl_pipeline_run_scans_without_result` call and reports no
  reconciliation entry, so the rows for `scan_2` and the third scan stay `'queued'`
- **AND** it writes a stderr line saying reconciliation was deferred to the status poller because
  `1` envelope failed retriably, and exits non-zero

#### Scenario: A retry of the step marks written a scan an earlier attempt failed to ingest

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, `run_manifest.wf-a.json` lists `scan_1` and `scan_2`,
  a third scan dispatched under `"wf-a"` is not listed, and an earlier attempt of this write-back
  step in the same Workflow wrote `scan_1` but failed retriably on `scan_2` (so it made no
  reconciliation call), and this attempt now ingests `scan_2` and re-delivers `scan_1` as a no-op
- **THEN** `scan_2`'s `cyl_pipeline_run_scans` row becomes `'written'` with
  `status_update_matched: true`, `scan_1`'s stays `'written'`, the command makes its one
  reconciliation call, which closes out only the third scan as `'failed'`, and the command exits
  zero

#### Scenario: Missing files and non-retriable failures do not defer reconciliation

- **WHEN** `ARGO_WORKFLOW_NAME` is `"wf-a"`, and the batch's only failures are a manifest-declared
  scan_key with no matching file and an envelope whose `status_update_matched` came back `false`
  (`retriable: false`)
- **THEN** the command still makes its one reconciliation call, writes no deferral line, and exits
  non-zero because of the missing scan_key

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
