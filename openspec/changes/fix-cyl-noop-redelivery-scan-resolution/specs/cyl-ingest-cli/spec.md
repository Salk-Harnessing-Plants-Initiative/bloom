## MODIFIED Requirements

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

### Requirement: Re-ingest is a benign, distinctly-reported no-op

The command SHALL report the RPC's first-writer-wins no-op — `was_noop=true`, which the RPC
returns without raising for an already-ingested envelope — as a success distinct from a real
error, exiting zero. Re-ingesting the same envelope therefore MUST NOT be reported as a failure.
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

#### Scenario: Re-delivery of a source first written outside any Bloom run is a benign no-op

- **WHEN** an envelope was first ingested by a manual `cyl ingest-result` (no `ARGO_WORKFLOW_NAME`)
  or by a hand-submitted Workflow with no `cyl_pipeline_run_scans` rows, and a Bloom-dispatched run
  later re-delivers it with its own `ARGO_WORKFLOW_NAME`, whose `'queued'` row is for that scan
- **THEN** the RPC's fallback marks that row `'written'` and returns `status_update_matched: true`,
  and the command (and `ingest_one_envelope`) reports the delivery as a benign, distinctly-reported
  no-op and exits zero
