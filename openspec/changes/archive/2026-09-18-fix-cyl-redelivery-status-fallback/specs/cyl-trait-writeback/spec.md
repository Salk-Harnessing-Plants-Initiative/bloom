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
'failed'`, where `<scan_id>` is looked up from any existing `cyl_pipeline_run_scans` row already
carrying this source's id (stamped by that source's original successful delivery) — not from this
delivery's own `image_ids` either. If no such row exists (this source was never delivered under
any workflow name), the fallback SHALL NOT run and the update remains at zero rows. Either way,
the no-op branch then returns without resolving a scan, without writing any trait, blob, or
registry row.

**On a non-no-op delivery** (the upsert wrote a new source row): the RPC proceeds to (5) resolve
the target scan from `provenance.inputs.image_ids`; (6) trait-name resolution and trait writes;
(7) blob writes; (8) when `p_argo_workflow_name` is non-null, an update of the matching
`cyl_pipeline_run_scans` row, joining on `argo_workflow_name = p_argo_workflow_name AND scan_id =
<the scan resolved in step 5> AND status != 'failed'`, setting `status = 'written'` and
`source_id` to the new source's id. This is the only statement that ever writes `source_id` onto
a `cyl_pipeline_run_scans` row, and it does so in the same statement that sets
`status = 'written'` — so `source_id IS NOT NULL` on that table always implies `status =
'written'`, which the no-op branch's fallback lookup above relies on. This RPC never writes
`'reused'`, which stays reserved for the separate, unimplemented pre-dispatch skip-if-done
mechanism `cyl_pipeline_run_scans`' own column comment documents.

Any validation or constraint failure SHALL abort the entire call, including every status update
above, so that no partial source, trait, registry, blob, or run-scan-status row persists
(all-or-nothing). The RPC SHALL return a `jsonb` summary reporting the source id, the resolved
scan id (null on a no-op), the trait and blob counts (equal to rows written), whether the call was
a no-op re-delivery, and — as `status_update_matched` — whether the (possibly fallback-assisted)
status update actually affected a row: `true`/`false` when `p_argo_workflow_name` was supplied,
`null` when it was omitted (not applicable, since no status update is ever attempted).

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
  looks up the scan id from the `"wf-a"` row (the one already carrying this source's id) and
  updates the `"wf-b"` row by `(argo_workflow_name = "wf-b", scan_id)`, setting its `status` to
  `'written'` and its `source_id` to the existing source's id; and the returned summary's
  `status_update_matched` is `true`

#### Scenario: A no-op re-delivery under a workflow name that never existed reports no match

- **WHEN** an already-ingested envelope's source has never had any `cyl_pipeline_run_scans` row
  stamped with its `source_id` (e.g. its only prior delivery omitted `p_argo_workflow_name`
  entirely), and it is re-delivered with a `p_argo_workflow_name` that matches no row
- **THEN** the call still reports `was_noop: true`, the fallback lookup finds no row to resolve a
  scan id from and does not run, and the returned summary's `status_update_matched` is `false` —
  unchanged from behavior before this change, since there was never a row for either update to
  find

#### Scenario: A no-op re-delivery under a workflow that never dispatched THIS scan finds no match

- **WHEN** an already-ingested envelope's source has an existing `cyl_pipeline_run_scans` row
  stamped with its `source_id` (its original delivery did supply a workflow name), and it is
  re-delivered with a `p_argo_workflow_name` for which no `cyl_pipeline_run_scans` row exists at
  all for this scan (distinct from the previous scenario: here the fallback's scan-id lookup
  succeeds, but its own targeted update finds no row to update under the new workflow name)
- **THEN** the call still reports `was_noop: true`, the fallback resolves a scan id from the
  existing row but its own update affects zero rows, and the returned summary's
  `status_update_matched` is `false` — a clean degrade, not an error

#### Scenario: The fallback chains correctly across a third re-delivery

- **WHEN** a scan is delivered successfully under `"wf-a"`, re-delivered as a no-op under a new
  `"wf-b"` (triggering the fallback, which stamps `"wf-b"`'s row with this source's id), and then
  re-delivered again as a no-op under a third new `"wf-c"`
- **THEN** `"wf-c"`'s fallback resolves a scan id from either of the two existing rows that now
  carry this source's id (both are guaranteed to name the same scan, since the fallback never
  re-derives scan id from a redelivery's own `image_ids`), and `"wf-c"`'s row is set to
  `'written'` with the correct `source_id`, exactly as `"wf-b"`'s was

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
  `cyl_pipeline_run_scans` row whose `status` is already `'failed'` (e.g.
  `fail_cyl_pipeline_run_scans_without_result` already closed it out earlier in the same batch)
- **THEN** the envelope's trait/source/blob rows are still written as usual (write-back itself is
  unaffected), but the `cyl_pipeline_run_scans` row's `status` remains `'failed'` — it is not
  overwritten to `'written'` — and the returned summary's `status_update_matched` is `false`

#### Scenario: A failed row under the ORIGINAL workflow does not block the fallback's own target

- **WHEN** a scan is delivered successfully under `"wf-a"`, that row is then marked `'failed'`,
  and the same envelope is re-delivered as a no-op under a **new** `argo_workflow_name = "wf-b"`
  whose own `cyl_pipeline_run_scans` row is still `'queued'`
- **THEN** the fallback resolves the scan id from the `"wf-a"` row as usual, but its own update —
  scoped to `"wf-b"`'s row by `scan_id` — is unaffected by `"wf-a"`'s `'failed'` status (a
  different row, matched on `argo_workflow_name = "wf-b"`) and still sets `"wf-b"`'s row to
  `'written'`; the guard only ever blocks resurrecting the row the update's own
  `argo_workflow_name` names, never a different workflow's row for the same scan

#### Scenario: A failed row under the NEW workflow is not resurrected by the fallback

- **WHEN** a scan is delivered successfully under `"wf-a"`, a second `cyl_pipeline_run_scans` row
  is dispatched for the same scan under a **new** `argo_workflow_name = "wf-b"`, that `"wf-b"`
  row is itself marked `'failed'` (not `"wf-a"`'s), and the same envelope is then re-delivered as
  a no-op under `"wf-b"`
- **THEN** the fallback still resolves the scan id from `"wf-a"`'s row, but its update — scoped
  to `"wf-b"`'s row by `scan_id` — matches zero rows because `"wf-b"`'s own row is `'failed'`;
  `"wf-b"`'s row stays `'failed'` with `source_id` unset, and the returned summary's
  `status_update_matched` is `false`

### Requirement: Write-back is idempotent and provenance-immutable

The RPC SHALL use the `cyl_trait_sources` insert as an atomic gate: `ON CONFLICT (idempotency_key)
DO NOTHING RETURNING id`. When a row is returned the call created the source and SHALL proceed to
write its traits and blobs; when no row is returned the run was already ingested and the RPC SHALL
**short-circuit to a pure no-op** — writing no further source, trait, blob, or registry rows,
performing only the per-scan status update (and its fallback) described in "Write-back RPC
ingests a ResultEnvelope" — and report the no-op. One run maps to exactly one source row, and
re-delivery of an already-ingested run changes nothing in the trait/blob tables regardless of
which `argo_workflow_name` re-delivers it (source, traits, and blobs are immutable; the stored
`metadata`/provenance is never overwritten, even if a re-delivered envelope carries a divergent
`metadata`, since volatile provenance fields are not in the producer's key hash). Because the
whole ingest is one transaction, the source row exists only if a prior delivery fully committed,
so a partial/failed delivery leaves nothing and a retry writes the full envelope.

#### Scenario: Re-delivery of the same envelope is a pure no-op

- **WHEN** the RPC is called twice with the same `ResultEnvelope`
- **THEN** exactly one `cyl_trait_sources` row exists, there are no duplicate `cyl_scan_traits`
  or `cyl_scan_intermediates` rows, and the second call reports a no-op

#### Scenario: Re-delivery with divergent metadata does not overwrite stored provenance

- **WHEN** the RPC is called again with the same `idempotency_key` but a different `metadata`
  payload
- **THEN** the originally stored `cyl_trait_sources.metadata` is unchanged and no further rows
  are written

#### Scenario: Re-delivery resolving to a different scan writes nothing to that scan

- **WHEN** the RPC is called again with the same `idempotency_key` but `image_ids` that resolve
  to a different scan than the original delivery
- **THEN** the call short-circuits on the existing source and writes no `cyl_scan_traits` or
  `cyl_scan_intermediates` rows against the different scan (the run identity, not the scan,
  governs) — and, if that different delivery also supplies a `p_argo_workflow_name` whose
  dispatched row is for the divergent scan, the fallback in "Write-back RPC ingests a
  ResultEnvelope" does not mark that row `'written'` either, since it resolves the scan id from
  the run of record's own row, not from this delivery's claim
