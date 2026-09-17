## MODIFIED Requirements

### Requirement: Re-ingest is a benign, distinctly-reported no-op

The command SHALL report the RPC's first-writer-wins no-op — `was_noop=true`, which the RPC
returns without raising for an already-ingested envelope — as a success distinct from a real
error, exiting zero. Re-ingesting the same envelope therefore MUST NOT be reported as a failure.
This SHALL hold end to end, not only for the RPC's response: a re-delivery whose producer
regenerated its artifacts MUST NOT fail at the blob-upload step before the RPC's gate is reached,
and it MUST NOT be reported as a failure on account of the RPC's `status_update_matched` field
regardless of which `ARGO_WORKFLOW_NAME` re-delivers it — a fresh pipeline run re-dispatching an
already-ingested scan under a **new** workflow name is exactly as benign a no-op as one
re-dispatched under the same workflow name, and the `cyl-trait-writeback` capability's fallback
update is what makes that true at the RPC layer.

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
