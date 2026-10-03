## MODIFIED Requirements

### Requirement: Write-back RPC ingests a ResultEnvelope

Bloom SHALL provide an in-database `SECURITY DEFINER` function (the write-back RPC, callable via
PostgREST) that takes one contract `ResultEnvelope` as `jsonb` (the `envelope` parameter,
unchanged in name from the original 1-arg signature — every caller, including `cyl-ingest-cli`'s
documented `client.rpc("insert_cyl_result_envelope", {"envelope": ...})` call shape, keys on this
exact name, and PostgREST resolves RPC parameters by name), plus an optional
`p_argo_workflow_name text DEFAULT NULL`, and, in a **single transaction**, writes the envelope
into `cyl_trait_sources`, `cyl_scan_traits` (via the `cyl_traits` registry), and
`cyl_scan_intermediates`. The function SHALL pin its owner deterministically and harden its
execution environment (`SET search_path` to a fixed safe value; schema-qualified writes;
parameterized value binding, never string-interpolated SQL). It MUST NOT be executable by
`PUBLIC`; `EXECUTE` SHALL be granted only to `bloom_writer`, `service_role`, `bloom_admin`, and
`bloom_workflows` (the scoped, non-interactive service identity used by cluster write-back pods).

The RPC performs, in order: (1) structural + contract-version validation; (2) idempotency-key
validation; (3) envelope self-consistency (one `scan_key` across `provenance` and every
trait/blob); (4) the source upsert via `ON CONFLICT (idempotency_key) DO NOTHING`, which
determines whether this call is a no-op re-delivery. **On a no-op** (no row returned by the
upsert): when `p_argo_workflow_name` is non-null, the RPC SHALL update the matching
`cyl_pipeline_run_scans` row by joining on `argo_workflow_name = p_argo_workflow_name AND
source_id = <the existing source's id> AND status != 'failed'` — never re-resolving `scan_id`
from this delivery's own `image_ids`, which the "same key, different scan" rule reserves for the
run of record alone. **When that update affects zero rows**, the RPC SHALL fall back to a second
update scoped to `argo_workflow_name = p_argo_workflow_name AND scan_id = <scan_id> AND status !=
'failed' AND (source_id IS NULL OR source_id = <the existing source's id>)`, setting `status =
'written'` and `source_id` to the existing source's id, where `<scan_id>` is the scan recorded on
the existing source's own row: its `cyl_trait_sources.scan_id`, read by primary key (stamped by
this RPC when it created the source, or by the recipe backfill from the source's stored
`image_ids`). Only when that column is NULL SHALL the RPC look `<scan_id>` up instead from any
existing `cyl_pipeline_run_scans` row already carrying this source's id. Neither lookup reads this
delivery's own `image_ids`, and the RPC MUST NOT resolve `<scan_id>` by reading `cyl_scan_traits`
or `cyl_scan_intermediates`. If neither lookup yields a scan, the fallback SHALL NOT run and the
update remains at zero rows. Because the targeted update is scoped to this call's
`argo_workflow_name`, a source whose scan this Workflow never dispatched still matches zero rows;
and because it skips a row already carrying a different source's id, a no-op never replaces the
source another delivery linked to that row. Either way, the no-op branch then returns without
resolving a scan for its return value (`scan_id` stays null), without writing any trait, blob, or
registry row.

**On a non-no-op delivery** (the upsert wrote a new source row): the RPC proceeds to (5) resolve
the target scan from `provenance.inputs.image_ids`; (6) trait-name resolution and trait writes;
(7) blob writes; (8) when `p_argo_workflow_name` is non-null, an update of the matching
`cyl_pipeline_run_scans` row, joining on `argo_workflow_name = p_argo_workflow_name AND scan_id =
<the scan resolved in step 5> AND status != 'failed'`, setting `status = 'written'` and
`source_id` to the new source's id. This update and the no-op fallback's targeted update are the
only statements that write `source_id` onto a `cyl_pipeline_run_scans` row, and each sets it in
the same statement that sets `status = 'written'` — so `source_id IS NOT NULL` on that table
always implies `status = 'written'`. This RPC never writes `'reused'`, which stays reserved for a
later phase in which the cluster-side skip-if-done check records a scan that needed no new work
(capability `cyl-pipeline-runs`, "`cyl_pipeline_run_scans` table").

Any validation or constraint failure SHALL abort the entire call, including every status update
above, so that no partial source, trait, registry, blob, or run-scan-status row persists
(all-or-nothing). The RPC SHALL return a `jsonb` summary reporting the source id, the resolved
scan id (null on a no-op), the trait and blob counts (equal to rows written), whether the call was
a no-op re-delivery, and — as `status_update_matched` — whether the (possibly fallback-assisted)
status update actually affected a row: `true`/`false` when `p_argo_workflow_name` was supplied,
`null` when it was omitted (not applicable, since no status update is ever attempted). This lets
a caller detect the case where write-back itself succeeds yet the `status != 'failed'` guard left
the scan's status unchanged (a late/out-of-order delivery arriving after the scan was already
closed out `'failed'`), rather than reporting a plain success with no signal that the run-level
counts will not reflect the data just written.

#### Scenario: A valid envelope writes source, trait, and blob rows in one transaction

- **WHEN** the RPC is called with a valid `ResultEnvelope`
- **THEN** exactly one `cyl_trait_sources` row is written (its `name` non-null, its `metadata`
  holding the `Provenance` object, its `idempotency_key` set), one `cyl_scan_traits` row per
  `TraitValue` (each carrying the source's `source_id`, the resolved `scan_id`, and a resolved
  `trait_id`), and one `cyl_scan_intermediates` row per `BlobRef`

#### Scenario: A partial-failure envelope persists nothing

- **WHEN** the RPC is called with an envelope whose source is valid but which contains one
  constraint-violating trait or blob row
- **THEN** the whole call is aborted and no `cyl_trait_sources`, `cyl_scan_traits`, `cyl_traits`,
  or `cyl_scan_intermediates` rows from that call persist

#### Scenario: The RPC return value reports ids, counts, and the no-op flag

- **WHEN** the RPC is called with a valid envelope and then called again with the same envelope
- **THEN** the first call returns the source id, resolved scan id, trait/blob counts, and a no-op
  flag that is false; the second returns the same source id, a null scan id, and a no-op flag
  that is true

#### Scenario: EXECUTE is granted only to the sanctioned roles, not PUBLIC

- **WHEN** execute permissions on the write-back RPC are introspected
- **THEN** `PUBLIC` cannot execute it and exactly `bloom_writer`, `service_role`, `bloom_admin`,
  and `bloom_workflows` hold `EXECUTE`

#### Scenario: bloom_workflows can call the RPC end-to-end

- **WHEN** a caller holding the `bloom_workflows` role calls the RPC with a valid
  `ResultEnvelope` (e.g. a cluster write-back pod authenticated as the scoped, non-interactive
  service identity)
- **THEN** the call succeeds exactly as it would for `bloom_writer`: the summary reports
  `was_noop: false` and the correct source id, resolved scan id, trait count, and blob count

#### Scenario: The definer can write after the lockdown (owner and FORCE RLS guard)

- **WHEN** the function's catalog metadata is introspected
- **THEN** it is `SECURITY DEFINER` with a pinned `search_path`, is owned by a role that can
  write all three tables under the post-lockdown policies, and none of the three tables has
  `FORCE ROW LEVEL SECURITY` enabled (which would re-subject the owner to RLS and break the only
  write path)

#### Scenario: Supplying a matching argo_workflow_name marks the scan written

- **WHEN** the RPC is called with a valid `ResultEnvelope` and `p_argo_workflow_name` equal to
  the value already stored on a `'queued'` `cyl_pipeline_run_scans` row for the envelope's
  resolved scan
- **THEN** the envelope's trait/source/blob rows are written as usual, **and** that
  `cyl_pipeline_run_scans` row's `status` becomes `'written'` and its `source_id` is set to the
  new source's id, in the same transaction, **and** the returned summary's
  `status_update_matched` is `true`

#### Scenario: A no-op re-delivery under the SAME workflow name still marks the scan written

- **WHEN** the RPC is called a second time with the same envelope (a no-op re-delivery per the
  idempotency requirement) and the same `p_argo_workflow_name` that already stamped that scan's
  row with this source's id
- **THEN** no new trait/source/blob rows are written (existing no-op behavior, unchanged), the
  primary `(argo_workflow_name, source_id)`-keyed update matches that row directly (no fallback
  needed), its `status` is (re-)set to `'written'`, and the returned summary's
  `status_update_matched` is `true`

#### Scenario: A no-op re-delivery under a NEW workflow name falls back to the run of record's scan

- **WHEN** a scan is first delivered successfully under `argo_workflow_name = "wf-a"` (stamping
  that row's `source_id`), the same scan is later re-dispatched under a **different**
  `argo_workflow_name = "wf-b"` (a fresh `cyl_pipeline_run_scans` row with `source_id IS NULL`,
  `status = 'queued'`), and the same envelope (same `idempotency_key`) is re-delivered with
  `p_argo_workflow_name = "wf-b"`
- **THEN** the call reports `was_noop: true`; the primary `(argo_workflow_name, source_id)`-keyed
  update matches zero rows under `"wf-b"` (its row's `source_id` is still `NULL`); the fallback
  takes the scan id from the existing source's own `cyl_trait_sources.scan_id` and
  updates the `"wf-b"` row by `(argo_workflow_name = "wf-b", scan_id)`, setting its `status` to
  `'written'` and its `source_id` to the existing source's id; and the returned summary's
  `status_update_matched` is `true`

#### Scenario: A no-op re-delivery of a source no run-scan row carries is marked written

- **WHEN** an already-ingested envelope's source has never had any `cyl_pipeline_run_scans` row
  stamped with its `source_id` — its only prior delivery omitted `p_argo_workflow_name` (a manual
  `cyl ingest-result`), or named a Workflow that has no `cyl_pipeline_run_scans` rows (a
  hand-submitted `argo submit`) — and it is re-delivered with `p_argo_workflow_name = "wf-b"`,
  whose `'queued'` row is for that source's recorded scan
- **THEN** the call reports `was_noop: true`, the fallback takes the scan id from the source's own
  `cyl_trait_sources.scan_id`, `"wf-b"`'s row becomes `'written'` with `source_id` set to the
  existing source's id, no new source, trait or blob row is written, and the returned summary's
  `status_update_matched` is `true`

#### Scenario: The source's recorded scan governs over a carrying run-scan row

- **WHEN** an already-ingested source's `cyl_trait_sources.scan_id` is scan S1, an existing
  `cyl_pipeline_run_scans` row carrying its `source_id` names a different scan S2, and it is
  re-delivered under a new `p_argo_workflow_name = "wf-b"` that has `'queued'` rows for both S1
  and S2
- **THEN** only `"wf-b"`'s S1 row becomes `'written'` with the existing source's id, `"wf-b"`'s S2
  row is unchanged, and `status_update_matched` is `true`

#### Scenario: A no-op does not replace another source already linked to this workflow's row

- **WHEN** `"wf-b"`'s `cyl_pipeline_run_scans` row for scan S is already `'written'` with
  `source_id` X (a fresh delivery under `"wf-b"`), and a different, already-ingested source Y whose
  recorded scan is S is re-delivered under `"wf-b"`
- **THEN** the call reports `was_noop: true`, the row stays `'written'` with `source_id` X, and
  `status_update_matched` is `false`

#### Scenario: The run-scan lookup is only a backup for a source with no recorded scan

- **WHEN** an already-ingested source has `cyl_trait_sources.scan_id` NULL but an existing
  `cyl_pipeline_run_scans` row carries its `source_id`, and it is re-delivered under a new
  `p_argo_workflow_name` whose `'queued'` row is for that row's scan
- **THEN** the fallback takes the scan id from the carrying row, the new Workflow's row becomes
  `'written'` with the existing source's id, and `status_update_matched` is `true`

#### Scenario: A no-op re-delivery with no recorded scan and no carrying row reports no match

- **WHEN** an already-ingested source has `cyl_trait_sources.scan_id` NULL and no
  `cyl_pipeline_run_scans` row carries its `source_id`, and it is re-delivered with a
  `p_argo_workflow_name`
- **THEN** the call still reports `was_noop: true`, the fallback finds no scan and does not run,
  no `cyl_pipeline_run_scans` row changes, and the returned summary's `status_update_matched` is
  `false`

#### Scenario: A no-op re-delivery under a workflow that never dispatched THIS scan finds no match

- **WHEN** an already-ingested envelope's source has a recorded scan (its
  `cyl_trait_sources.scan_id`), and it is re-delivered with a `p_argo_workflow_name` for which no
  `cyl_pipeline_run_scans` row exists at all for that scan (the fallback's scan-id lookup
  succeeds, but its own targeted update finds no row to update under that workflow name)
- **THEN** the call still reports `was_noop: true`, the fallback resolves the scan id but its own
  update affects zero rows, no `cyl_pipeline_run_scans` row changes, and the returned summary's
  `status_update_matched` is `false` — a clean degrade, not an error

#### Scenario: The fallback chains correctly across a third re-delivery

- **WHEN** a scan is delivered successfully under `"wf-a"`, re-delivered as a no-op under a new
  `"wf-b"` (triggering the fallback, which stamps `"wf-b"`'s row with this source's id), and then
  re-delivered again as a no-op under a third new `"wf-c"`
- **THEN** `"wf-c"`'s fallback resolves the same scan id from the source's own
  `cyl_trait_sources.scan_id` (the fallback never re-derives scan id from a redelivery's own
  `image_ids`), and `"wf-c"`'s row is set to `'written'` with the correct `source_id`, exactly as
  `"wf-b"`'s was

#### Scenario: Omitting argo_workflow_name leaves cyl_pipeline_run_scans untouched

- **WHEN** the RPC is called without `p_argo_workflow_name` (the existing manual/ad-hoc `cyl
  ingest-result` invocation shape, unchanged by this parameter's addition)
- **THEN** the envelope is ingested exactly as before, no `cyl_pipeline_run_scans` row is read or
  written, and the returned summary's `status_update_matched` is `null`

#### Scenario: A non-matching argo_workflow_name on a first delivery affects zero rows, not an error

- **WHEN** the RPC is called on a genuine first delivery with a `p_argo_workflow_name` that
  matches no `cyl_pipeline_run_scans` row for the resolved scan
- **THEN** the envelope's trait/source/blob rows are still written as usual, the call succeeds
  without error, having updated zero `cyl_pipeline_run_scans` rows, and the returned summary's
  `status_update_matched` is `false`

#### Scenario: A rolled-back call does not leave a partial status update

- **WHEN** the RPC is called with an invalid envelope (per any existing validation requirement)
  and a `p_argo_workflow_name` that would otherwise match a `cyl_pipeline_run_scans` row via
  either the primary update or the fallback
- **THEN** the whole call is aborted, and that row's `status` and `source_id` are unchanged — no
  status update, primary or fallback, survives a rolled-back transaction

#### Scenario: A late delivery after the scan was already marked failed does not resurrect it

- **WHEN** the RPC is called with a valid envelope and a `p_argo_workflow_name` matching a
  `cyl_pipeline_run_scans` row whose `status` is already `'failed'` (e.g. the
  status poller already closed it out with `fail_cyl_pipeline_run_scans_without_result` after its
  Workflow finished, and the envelope is then delivered again under that workflow name)
- **THEN** the envelope's trait/source/blob rows are still written as usual (write-back itself is
  unaffected), but the `cyl_pipeline_run_scans` row's `status` remains `'failed'` — it is not
  overwritten to `'written'` — and the returned summary's `status_update_matched` is `false`

#### Scenario: A failed row under the ORIGINAL workflow does not block the fallback's own target

- **WHEN** a scan is delivered successfully under `"wf-a"`, that row is then marked `'failed'`,
  and the same envelope is re-delivered as a no-op under a **new** `argo_workflow_name = "wf-b"`
  whose own `cyl_pipeline_run_scans` row is still `'queued'`
- **THEN** the fallback resolves the scan id as usual, but its own update —
  scoped to `"wf-b"`'s row by `scan_id` — is unaffected by `"wf-a"`'s `'failed'` status (a
  different row, matched on `argo_workflow_name = "wf-b"`) and still sets `"wf-b"`'s row to
  `'written'`; the guard only ever blocks resurrecting the row the update's own
  `argo_workflow_name` names, never a different workflow's row for the same scan

#### Scenario: A failed row under the NEW workflow is not resurrected by the fallback

- **WHEN** a scan is delivered successfully under `"wf-a"`, a second `cyl_pipeline_run_scans` row
  is dispatched for the same scan under a **new** `argo_workflow_name = "wf-b"`, that `"wf-b"`
  row is itself marked `'failed'` (not `"wf-a"`'s), and the same envelope is then re-delivered as
  a no-op under `"wf-b"`
- **THEN** the fallback still resolves the scan id, but its update — scoped
  to `"wf-b"`'s row by `scan_id` — matches zero rows because `"wf-b"`'s own row is `'failed'`;
  `"wf-b"`'s row stays `'failed'` with `source_id` unset, and the returned summary's
  `status_update_matched` is `false`

#### Scenario: The same guard applies to a no-op re-delivery arriving after the scan was failed

- **WHEN** the RPC is called a second time with the same envelope (a no-op re-delivery) and the
  same `p_argo_workflow_name`, but the matching `cyl_pipeline_run_scans` row (already stamped with
  this source's id by the first call) was marked `'failed'` between the first and second calls
- **THEN** the second call still reports `was_noop: true` (write-back's own idempotency is
  unaffected), but neither the primary `(argo_workflow_name, source_id)`-keyed update nor the
  fallback's `(argo_workflow_name, scan_id)`-keyed update changes that row — both carry the
  identical `AND status != 'failed'` guard as the non-no-op branch's update — so the row stays
  `'failed'` and the returned summary's `status_update_matched` is `false`

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
function at most once per invocation, after ingesting every envelope discovered for the batch, and only
when no envelope it attempted failed retriably (capability `cyl-batch-ingest-result`), passing the
`ARGO_WORKFLOW_NAME` environment variable Argo sets on the write-back container. When it skips the call
because a retry of the step could still write a result, the status poller makes the same call once the
Workflow has finished (capability `cyl-pipeline-status-polling`) — and SHALL skip the call entirely when that
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
