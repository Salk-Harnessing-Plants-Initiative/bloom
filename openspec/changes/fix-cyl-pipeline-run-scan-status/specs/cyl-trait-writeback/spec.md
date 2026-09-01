## MODIFIED Requirements

### Requirement: Write-back RPC ingests a ResultEnvelope

Bloom SHALL provide an in-database `SECURITY DEFINER` function (the write-back RPC, callable via
PostgREST) that takes one contract `ResultEnvelope` as `jsonb` (the `envelope` parameter, unchanged
in name from the existing signature — every caller, including `cyl-ingest-cli`'s documented
`client.rpc("insert_cyl_result_envelope", {"envelope": ...})` call shape, keys on this exact name,
and PostgREST resolves RPC parameters by name, so renaming it would break every existing caller),
plus a new optional `p_argo_workflow_name text DEFAULT NULL`, and, in a **single transaction**,
writes the envelope into `cyl_trait_sources`, `cyl_scan_traits` (via the `cyl_traits` registry), and
`cyl_scan_intermediates`. The function SHALL pin its owner deterministically and harden its execution
environment (`SET search_path` to a fixed safe value; schema-qualified writes; parameterized value
binding, never string-interpolated SQL). It MUST NOT be executable by `PUBLIC`; `EXECUTE` SHALL be
granted only to `bloom_writer`, `service_role`, `bloom_admin`, and `bloom_workflows` (the scoped,
non-interactive service identity used by cluster write-back pods). The RPC performs, in order: (1)
structural + contract-version validation; (2) idempotency-key validation; (3) scan resolution; (4)
the source upsert; (5) trait-name resolution and trait writes; (6) blob writes; (7) **when
`p_argo_workflow_name` is non-null**, an `UPDATE` of the matching `cyl_pipeline_run_scans` row —
matched on `argo_workflow_name = p_argo_workflow_name AND scan_id = <the scan resolved in step 3>
AND status != 'failed'` (the trailing guard mirrors `complete_cyl_pipeline_batch`'s existing
`AND status != 'failed'` convention: a write-back call that lands after this scan was already
closed out as `'failed'` — by the failure-marking RPC below, on a genuinely late/out-of-order
delivery — must not silently resurrect it, since a `'failed'` run can already have dropped out of
the status poller's candidate set and nothing would ever recompute its parent run's counts again) —
setting its `status` to `'written'` and its `source_id` to the resolved source id, on both a normal
delivery and a no-op re-delivery alike (this RPC's own idempotent re-delivery is not the same
event as the pre-dispatch "cluster-side skip-if-done" mechanism that is the sole writer of
`'reused'` per the `cyl-pipeline-runs` capability's existing column documentation; this RPC never
writes `'reused'`). If no matching `cyl_pipeline_run_scans` row exists (e.g. `p_argo_workflow_name`
doesn't match any row, or the matching row was already `'failed'`), step (7) affects zero rows and
is not an error. Any validation or constraint failure SHALL abort the entire call, including step
(7), so that no partial source, trait, registry, blob, or run-scan-status rows persist
(all-or-nothing). The RPC SHALL return a `jsonb` summary reporting the source id, the resolved scan
id (null on a no-op re-delivery), the trait and blob counts (equal to rows written), and whether the
call was a no-op re-delivery.

#### Scenario: A valid envelope writes source, trait, and blob rows in one transaction

- **WHEN** the RPC is called with a valid `ResultEnvelope`
- **THEN** exactly one `cyl_trait_sources` row is written (its `name` non-null, its `metadata` holding
  the `Provenance` object, its `idempotency_key` set), one `cyl_scan_traits` row per `TraitValue`
  (each carrying the source's `source_id`, the resolved `scan_id`, and a resolved `trait_id`), and one
  `cyl_scan_intermediates` row per `BlobRef`

#### Scenario: A partial-failure envelope persists nothing

- **WHEN** the RPC is called with an envelope whose source is valid but which contains one
  constraint-violating trait or blob row
- **THEN** the whole call is aborted and no `cyl_trait_sources`, `cyl_scan_traits`, `cyl_traits`, or
  `cyl_scan_intermediates` rows from that call persist

#### Scenario: The RPC return value reports ids, counts, and the no-op flag

- **WHEN** the RPC is called with a valid envelope and then called again with the same envelope
- **THEN** the first call returns the source id, resolved scan id, trait/blob counts, and a no-op flag
  that is false; the second returns the same source id, a null scan id, and a no-op flag that is true

#### Scenario: EXECUTE is granted only to the sanctioned roles, not PUBLIC

- **WHEN** execute permissions on the write-back RPC are introspected
- **THEN** `PUBLIC` cannot execute it and exactly `bloom_writer`, `service_role`, `bloom_admin`, and
  `bloom_workflows` hold `EXECUTE`

#### Scenario: bloom_workflows can call the RPC end-to-end

- **WHEN** a caller holding the `bloom_workflows` role calls the RPC with a valid `ResultEnvelope`
  (e.g. a cluster write-back pod authenticated as the scoped, non-interactive service identity)
- **THEN** the call succeeds exactly as it would for `bloom_writer`: the summary reports `was_noop:
  false` and the correct source id, resolved scan id, trait count, and blob count

#### Scenario: The definer can write after the lockdown (owner and FORCE RLS guard)

- **WHEN** the function's catalog metadata is introspected
- **THEN** it is `SECURITY DEFINER` with a pinned `search_path`, is owned by a role that can write all
  three tables under the post-lockdown policies, and none of the three tables has `FORCE ROW LEVEL
  SECURITY` enabled (which would re-subject the owner to RLS and break the only write path)

#### Scenario: Supplying a matching argo_workflow_name marks the scan written

- **WHEN** the RPC is called with a valid `ResultEnvelope` and `p_argo_workflow_name` equal to the
  value already stored on a `'queued'` `cyl_pipeline_run_scans` row for the envelope's resolved scan
- **THEN** the envelope's trait/source/blob rows are written as usual, **and** that
  `cyl_pipeline_run_scans` row's `status` becomes `'written'` and its `source_id` is set to the new
  source's id, in the same transaction

#### Scenario: A no-op re-delivery with argo_workflow_name still marks the scan written

- **WHEN** the RPC is called a second time with the same envelope (a no-op re-delivery per the
  idempotency requirement) and the same `p_argo_workflow_name`
- **THEN** no new trait/source/blob rows are written (existing no-op behavior, unchanged), **and** the
  matching `cyl_pipeline_run_scans` row's `status` is (re-)set to `'written'` — this RPC's own
  idempotent re-delivery never produces `'reused'`, which remains reserved for the separate,
  pre-dispatch skip-if-done mechanism

#### Scenario: Omitting argo_workflow_name leaves cyl_pipeline_run_scans untouched

- **WHEN** the RPC is called without `p_argo_workflow_name` (the existing manual/ad-hoc `cyl
  ingest-result` invocation shape, unchanged by this parameter's addition)
- **THEN** the envelope is ingested exactly as before, and no `cyl_pipeline_run_scans` row is read or
  written

#### Scenario: A non-matching argo_workflow_name affects zero rows, not an error

- **WHEN** the RPC is called with a `p_argo_workflow_name` that matches no `cyl_pipeline_run_scans`
  row for the resolved scan
- **THEN** the envelope's trait/source/blob rows are still written as usual, and the call succeeds
  without error, having updated zero `cyl_pipeline_run_scans` rows

#### Scenario: A rolled-back call does not leave a partial status update

- **WHEN** the RPC is called with an invalid envelope (per any existing validation requirement) and a
  `p_argo_workflow_name` that would otherwise match a `cyl_pipeline_run_scans` row
- **THEN** the whole call is aborted, and that row's `status` is unchanged — no write-back status
  update survives a rolled-back transaction

#### Scenario: A late delivery after the scan was already marked failed does not resurrect it

- **WHEN** the RPC is called with a valid envelope and a `p_argo_workflow_name` matching a
  `cyl_pipeline_run_scans` row whose `status` is already `'failed'` (e.g.
  `fail_cyl_pipeline_run_scans_without_result` already closed it out earlier in the same batch, and
  this delivery is a genuinely late/out-of-order retry)
- **THEN** the envelope's trait/source/blob rows are still written as usual (write-back itself is
  unaffected), but the `cyl_pipeline_run_scans` row's `status` remains `'failed'` — it is not
  overwritten to `'written'`

## ADDED Requirements

### Requirement: Failure-marking RPC closes out scans that never produced a result

Bloom SHALL provide a `SECURITY DEFINER` function
`fail_cyl_pipeline_run_scans_without_result(p_argo_workflow_name text, p_error_message text DEFAULT
NULL) RETURNS integer` that, for every `cyl_pipeline_run_scans` row matching `argo_workflow_name =
p_argo_workflow_name` and currently `status = 'queued'`, sets `status = 'failed'`, `error_message =
p_error_message` (when supplied), and `updated_at = now()`, and returns the number of rows updated.
A row already `'written'`, `'reused'`, or `'failed'` for this workflow name is left untouched — this
function only closes out scans write-back never resolved either way. `EXECUTE` SHALL be revoked from
`PUBLIC`, `anon`, and `authenticated`, and granted only to `bloom_workflows`, matching this program's
established `SECURITY DEFINER` wrapper convention. `bloomctl cyl batch-ingest-result` SHALL call this
function once, after ingesting every envelope discovered for the batch, passing the `ARGO_WORKFLOW_NAME`
environment variable Argo sets on the write-back container — and SHALL skip the call entirely when that
environment variable is unset (a manual/local batch run with no pipeline-run context), leaving all
`cyl_pipeline_run_scans` rows (if any happen to exist) untouched.

#### Scenario: A scan with no envelope is marked failed

- **WHEN** `fail_cyl_pipeline_run_scans_without_result` is called with an `argo_workflow_name` for
  which a `cyl_pipeline_run_scans` row is still `'queued'` (its scan's prediction never produced an
  envelope for write-back to ingest)
- **THEN** that row's `status` becomes `'failed'`, its `error_message` is set to the supplied value,
  and the function returns `1`

#### Scenario: A scan already written by this batch is left untouched

- **WHEN** `fail_cyl_pipeline_run_scans_without_result` is called for a workflow name whose batch
  included one scan already marked `'written'` earlier in the same `batch-ingest-result` invocation
- **THEN** that row's `status`, `source_id`, and `updated_at` are unchanged, and it is not counted in
  the function's returned count

#### Scenario: Calling it twice for the same workflow name is a harmless no-op the second time

- **WHEN** `fail_cyl_pipeline_run_scans_without_result` is called twice in a row for the same
  `argo_workflow_name` (e.g. the write-back step's `retryStrategy` re-runs the whole container)
- **THEN** the first call marks the remaining `'queued'` rows `'failed'` and returns their count; the
  second call returns `0` and leaves every row exactly as the first call left it

#### Scenario: A workflow name matching no rows returns zero, not an error

- **WHEN** `fail_cyl_pipeline_run_scans_without_result` is called with an `argo_workflow_name` that
  matches no `cyl_pipeline_run_scans` row at all
- **THEN** the call succeeds and returns `0`

#### Scenario: EXECUTE is denied to every role except bloom_workflows

- **WHEN** `has_function_privilege` is checked for `anon`, `authenticated`, the implicit `PUBLIC`
  grantee, and `bloom_user`/`bloom_writer`/`bloom_admin` against this function's signature
- **THEN** each reports `EXECUTE` as `false`
- **AND** the same check for `bloom_workflows` reports `true`
