# cyl-trait-writeback Specification

## Purpose
Defines the Bloom database schema that receives sleap-roots cylinder pipeline results: the
provenance/idempotency anchors on `cyl_trait_sources` and the per-scan artifact-pointer table
`cyl_scan_intermediates` (one row per `.slp` per root type), with their constraints and role-based
RLS. This is the write target the write-back path populates; the schema is kept in agreement with the
pinned `sleap-roots-contracts` envelope (see the `contract-pinning` capability).
## Requirements
### Requirement: Trait source provenance column

`cyl_trait_sources` SHALL provide a nullable `metadata` column of type `jsonb` capable of
storing a JSON object (the contract `Provenance` envelope). Bloom SHALL treat the value as
opaque jsonb and MUST NOT validate its internal shape at the database layer. The column
MUST be nullable so that legacy or non-pipeline source rows (which have no envelope) remain
valid. (Writing an envelope for a specific pipeline run is the write-back RPC's behavior,
specified separately.)

#### Scenario: Metadata column persists and round-trips a JSON object

- **WHEN** a JSON object is stored in `cyl_trait_sources.metadata`
- **THEN** it is persisted and read back unchanged as `jsonb` (`jsonb_typeof` is `object`)

#### Scenario: Metadata accepts non-object jsonb (no shape validation)

- **WHEN** a non-object jsonb value (e.g. an array or scalar) is stored in
  `cyl_trait_sources.metadata`
- **THEN** the database accepts it, confirming no database-layer shape validation

#### Scenario: Legacy source rows remain valid

- **WHEN** a `cyl_trait_sources` row exists with only `id` and `name` set
- **THEN** its `metadata` column is `NULL` and the row is valid

### Requirement: Trait source idempotency anchor

`cyl_trait_sources` SHALL provide a nullable `idempotency_key` column of type `text` with a
`UNIQUE` constraint, so that each pipeline run maps to at most one source row and
re-delivery of the same run cannot create a duplicate source. The column MUST reject the
empty string via a `CHECK` constraint (`idempotency_key IS NULL OR length(idempotency_key)
> 0`), because the contract defaults this field to `""` and a shared empty-string key would
collapse all keyless runs onto one source row. The column MUST be nullable so that legacy
or non-pipeline source rows without a key coexist.

#### Scenario: Duplicate non-null key is rejected

- **WHEN** a second source row is inserted with the same non-null `idempotency_key` as an
  existing row
- **THEN** the database rejects the insert with a unique-constraint violation

#### Scenario: Empty-string key is rejected

- **WHEN** a source row is inserted with `idempotency_key = ''`
- **THEN** the database rejects the insert with a check-constraint violation

#### Scenario: Multiple keyless sources are permitted

- **WHEN** more than one source row is inserted with a `NULL` `idempotency_key`
- **THEN** all such inserts succeed and the UNIQUE constraint is not violated

### Requirement: Additive, non-destructive provenance migration

The migration that adds the provenance and idempotency columns SHALL be **additive only**:
it MUST NOT drop or rewrite existing columns or data, so a single forward `supabase db push`
applies it safely to the persistent database and adding columns MUST NOT break existing
inserts. A companion manual rollback script SHALL be provided under `supabase/rollbacks/`
that, when applied, removes the added columns and their constraints, returning
`cyl_trait_sources` to its prior `(id, name)` shape.

#### Scenario: Existing inserts keep working after the migration

- **WHEN** the migration has been applied and a row is inserted supplying only `name`
- **THEN** the insert succeeds and `metadata` and `idempotency_key` default to `NULL`

#### Scenario: Rollback script restores the prior schema

- **WHEN** the companion rollback script is applied to a database where the migration had
  been applied
- **THEN** `cyl_trait_sources` no longer has the `metadata` or `idempotency_key` columns
  nor their UNIQUE/CHECK constraints

### Requirement: Per-scan intermediates table

Bloom SHALL provide a `cyl_scan_intermediates` table holding one row per per-scan pipeline
artifact file (today: one SLEAP `.slp` prediction file per root type per scan). The table SHALL
have a `BIGINT` identity primary key and the columns `source_id` (`BIGINT NOT NULL`), `scan_id`
(`BIGINT NOT NULL`), `kind` (`text NOT NULL`), `root_type` (`text NOT NULL`), `s3_location`
(`text`, nullable), `box_link` (`text`, nullable), `checksum` (`text`, nullable), and `file_size`
(`bigint`, nullable). `s3_location` is the canonical Bloom-served object-storage pointer and
`box_link` is a human-shareable Box link; `checksum` and `file_size` tie the two copies together
and allow partial-upload detection. The table SHALL NOT carry a pipeline-identity column — the
pipeline that produced a row is recoverable from provenance via `source_id`.

#### Scenario: Table exists with the expected columns and types

- **WHEN** the applied database is introspected
- **THEN** `cyl_scan_intermediates` exists with `source_id`/`scan_id` of type `bigint`,
  `kind`/`root_type`/`s3_location`/`box_link`/`checksum` of type `text`, and `file_size` of type
  `bigint`

#### Scenario: A fully specified artifact row persists

- **WHEN** a row is inserted with valid `source_id`, `scan_id`, `kind = 'predictions_slp'`,
  `root_type = 'primary'`, an `s3_location`, a `checksum`, and a `file_size`
- **THEN** the insert succeeds and the values read back unchanged

### Requirement: Intermediates table foreign keys

`cyl_scan_intermediates.source_id` SHALL be a foreign key to `cyl_trait_sources(id)` and
`cyl_scan_intermediates.scan_id` SHALL be a foreign key to `cyl_scans(id)`. These two foreign keys
are the link between an artifact and both the run that produced it (provenance) and the scan it
belongs to; the same `(source_id, scan_id)` pair links the artifact to its sibling
`cyl_scan_traits` rows, so no column is added to `cyl_scan_traits`.

#### Scenario: Foreign keys reference the provenance and scan tables

- **WHEN** the constraints on `cyl_scan_intermediates` are introspected by type and referenced
  table (`contype = 'f'`, `confrelid`)
- **THEN** there is a foreign key from `source_id` to `cyl_trait_sources` and a foreign key from
  `scan_id` to `cyl_scans`

#### Scenario: A row referencing a missing scan is rejected

- **WHEN** a row is inserted with a `scan_id` that does not exist in `cyl_scans`
- **THEN** the database rejects the insert with a foreign-key violation

### Requirement: Intermediates require at least one storage location

`cyl_scan_intermediates` SHALL enforce, via a `CHECK` constraint, that at least one of
`s3_location` or `box_link` is non-null, mirroring the contract `BlobRef` `anyOf` that requires at
least one location. A row with both locations null SHALL be rejected.

#### Scenario: A row with both locations null is rejected

- **WHEN** a row is inserted with both `s3_location` and `box_link` set to `NULL`
- **THEN** the database rejects the insert with a check-constraint violation

#### Scenario: A row with only a Box link is accepted

- **WHEN** a row is inserted with `s3_location = NULL` and a non-null `box_link` (and otherwise
  valid values)
- **THEN** the insert succeeds

### Requirement: Intermediates use strict kind and root-type vocabularies

`cyl_scan_intermediates.kind` SHALL be constrained by a `CHECK` to the artifact kinds defined by
the pinned contract `BlobRef.kind` enum (currently `predictions_slp`), and `root_type` SHALL be
constrained by a `CHECK` to the strict root-type vocabulary `primary`, `lateral`, `crown` (the
union across pipeline types). This `root_type` `CHECK` constraint is the single source of truth for
the accepted root-type vocabulary; other documents describe it but do not redefine it. Values
outside these vocabularies SHALL be rejected.

#### Scenario: An unknown kind is rejected

- **WHEN** a row is inserted with `kind = 'h5'` (a value not in the current contract enum)
- **THEN** the database rejects the insert with a check-constraint violation

#### Scenario: An unknown root type is rejected

- **WHEN** a row is inserted with `root_type = 'seminal'` (not in the strict vocabulary)
- **THEN** the database rejects the insert with a check-constraint violation

#### Scenario: Each vocabulary root type is accepted

- **WHEN** a valid row is inserted with `root_type` set to each of `primary`, `lateral`, and `crown`
  in turn (with `kind = 'predictions_slp'` and a valid location)
- **THEN** every such insert succeeds, confirming the full accepted vocabulary

#### Scenario: Optional integrity columns may be null

- **WHEN** a valid row is inserted with a location and `kind`/`root_type` but `checksum` and
  `file_size` left unset
- **THEN** the insert succeeds with `checksum` and `file_size` NULL

### Requirement: One intermediate per run, scan, kind, and root type

`cyl_scan_intermediates` SHALL enforce a `UNIQUE` constraint on
`(source_id, scan_id, kind, root_type)` so that a single pipeline run records at most one artifact
of a given kind and root type per scan, giving the write-back path a deterministic upsert key while
preserving history across distinct runs (which have distinct `source_id`).

#### Scenario: Duplicate (source, scan, kind, root_type) is rejected

- **WHEN** a second row is inserted with the same `(source_id, scan_id, kind, root_type)` as an
  existing row
- **THEN** the database rejects the insert with a unique-constraint violation

#### Scenario: The same artifact from a different run is permitted

- **WHEN** a row is inserted with the same `(scan_id, kind, root_type)` but a different `source_id`
  than an existing row
- **THEN** the insert succeeds, preserving both runs' artifacts

### Requirement: Intermediates table role-based access control

`cyl_scan_intermediates` SHALL have row-level security enabled with policies following Bloom's
role model: `bloom_admin` SHALL have full access (`FOR ALL`); and `bloom_agent`, `bloom_user`, and
`bloom_writer` SHALL have read-only (`SELECT`) access. Direct `INSERT`/`UPDATE`/`DELETE` to the table
SHALL NOT be permitted to any role other than `bloom_admin` — all writes go through the write-back
RPC (its `SECURITY DEFINER` owner), so the table SHALL NOT define any `INSERT`/`UPDATE`/`DELETE` policy
for `bloom_writer`, `bloom_user`, or `bloom_agent`, and SHALL NOT use the legacy permissive
`authenticated` policies. The migration SHALL issue the table-level `GRANT`s that gate PostgREST read
access for those roles. Read-only/no-direct-write posture is enforced by the absence of write policies
even though a standing default table-level GRANT to some roles is permissive (so RLS, not the GRANT,
is the write gate). These guarantees are verified by exercising each role (`SET LOCAL ROLE`) against
the table, not by catalog introspection alone, because the migration/test connection role bypasses
RLS.

#### Scenario: Only admin can write directly; the write-back role cannot

- **WHEN** a session assumes `bloom_writer` (and likewise `bloom_user`/`bloom_agent`) and attempts to
  `INSERT` or `UPDATE` a row
- **THEN** the write is rejected (no permitting policy), and only `bloom_admin` may write the table
  directly

#### Scenario: Every role can read

- **WHEN** a session assumes each of `bloom_admin`, `bloom_agent`, `bloom_user`, and `bloom_writer`
  and runs `SELECT` against the table
- **THEN** each read is permitted

#### Scenario: RLS is enabled with the expected policy set (drift detector)

- **WHEN** the table's RLS state and policies are introspected
- **THEN** row-level security is enabled and exactly the expected policies are present
  (`bloom_admin` all; `bloom_agent`, `bloom_user`, and `bloom_writer` `SELECT`) with no
  `INSERT`/`UPDATE`/`DELETE` policy for any non-admin role

### Requirement: Additive, non-destructive intermediates migration

The migration that creates `cyl_scan_intermediates` SHALL be **additive only** — it MUST NOT drop
or rewrite existing tables, columns, or data — so a single forward `supabase db push` applies it
safely. A companion manual rollback script SHALL be provided under `supabase/rollbacks/` that drops
the table, returning the schema to its prior state. The tracked Supabase `database.types.ts` files
SHALL be regenerated to include the new table.

#### Scenario: Forward migration adds the table without touching existing objects

- **WHEN** the migration is applied to a database that already has `cyl_trait_sources` and
  `cyl_scans`
- **THEN** `cyl_scan_intermediates` is created and the pre-existing tables are unchanged

#### Scenario: Rollback script removes the table

- **WHEN** the companion rollback script is applied to a database where the migration had been
  applied
- **THEN** `cyl_scan_intermediates` no longer exists

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

### Requirement: Write-back validates the idempotency key

The RPC SHALL treat `provenance.idempotency_key` as an opaque producer-derived identity and MUST NOT
recompute it. It SHALL reject an envelope whose `provenance.idempotency_key` is empty or absent,
writing nothing. It SHALL write the dedup-anchor `idempotency_key` column and the stored Provenance
`metadata` from the same envelope field, so every written row satisfies
`idempotency_key == metadata->>'idempotency_key'` — the invariant the sole-writer RPC maintains (change
A deliberately omitted a DB CHECK for it because that would break the nullable/opaque-jsonb columns).

#### Scenario: Empty or absent idempotency key is rejected

- **WHEN** the RPC is called with `provenance.idempotency_key = ''` or with the key absent
- **THEN** the call is rejected and nothing is written

#### Scenario: Every written row satisfies the key/metadata invariant

- **WHEN** the RPC writes a source row
- **THEN** that row's `idempotency_key` column equals its `metadata->>'idempotency_key'`

### Requirement: Write-back validates the contract version

The RPC SHALL validate that `provenance.contract_version` matches the contract version Bloom is
pinned to (`0.1.0a7`), and SHALL reject any envelope whose `contract_version` does not match, writing
nothing. This anchors every written row to a known contract-of-origin. The match SHALL be
**prefix-tolerant**: a **single lowercase leading `v`** is normalized away on both the incoming value
and the pinned version before comparison, so the bare package-version form the emitter stamps
(`0.1.0a7`, read from the installed `sleap-roots-contracts` distribution) and the `v`-prefixed
git-tag/`$id` form (`v0.1.0a7`) are both accepted. Normalization is scoped to that single lowercase
`v`: an uppercase `V`, a doubled `vv`, surrounding whitespace, or any build/local segment is NOT the
pinned version and SHALL be rejected. An absent or empty `contract_version` SHALL be rejected (the
comparison operates on the coalesced normalized strings, so a `NULL`/absent value collapses to the
empty string and fails the match rather than passing). Only the single pinned version (in either
accepted form) is accepted — any other version, including any previously pinned version
(`0.1.0a3`/`v0.1.0a3`, `0.1.0a2`/`v0.1.0a2`), SHALL be rejected.

#### Scenario: Matching bare contract version is accepted

- **WHEN** the RPC is called with `provenance.contract_version` equal to the pinned version in its
  bare package form (`0.1.0a7`)
- **THEN** the envelope is ingested

#### Scenario: Matching v-prefixed contract version is accepted

- **WHEN** the RPC is called with `provenance.contract_version` equal to the pinned version with a
  single lowercase leading `v` (`v0.1.0a7`)
- **THEN** the envelope is ingested, the leading `v` having been normalized away before comparison

#### Scenario: A previously pinned contract version is rejected

- **WHEN** the RPC is called with `provenance.contract_version` set to a previously pinned version
  in either form (`0.1.0a3`/`v0.1.0a3`, or `0.1.0a2`/`v0.1.0a2`)
- **THEN** the call is rejected and nothing is written (each re-pin is a hard cutover, not a
  compatibility set)

#### Scenario: A non-pinned or malformed version form is rejected

- **WHEN** the RPC is called with `provenance.contract_version` set to any other value — an unrelated
  version, an uppercase `V0.1.0a7`, a doubled `vv0.1.0a7`, a trailing-whitespace `0.1.0a7 `, or a
  near-miss `0.1.0a70`
- **THEN** the call is rejected and nothing is written

#### Scenario: Absent or empty contract version is rejected

- **WHEN** the RPC is called with `provenance.contract_version` absent or set to the empty string
- **THEN** the call is rejected and nothing is written

### Requirement: Write-back resolves the scan from input image ids

The RPC SHALL resolve the target scan from `provenance.inputs.image_ids` (the contract carries no
Bloom scan id) by mapping those ids to `cyl_images.scan_id`, and SHALL NOT use `provenance.scan_key`
for resolution (Bloom stores no `scan_key` column). Over the **distinct** set of requested ids, it
SHALL require that every distinct id matches a `cyl_images` row with a non-null `scan_id` and that all
matches share **exactly one** distinct `scan_id` (so a legitimately repeated `image_id` does not cause
a false rejection). It SHALL reject — writing nothing — an envelope whose `image_ids` are empty, are
non-numeric, do not all match existing images, or resolve to more than one scan. The single resolved
`scan_id` SHALL be used for all trait and blob rows written from the envelope.

#### Scenario: Images belonging to one scan resolve to that scan

- **WHEN** the RPC is called with an envelope whose `image_ids` all match images of a single Bloom scan
- **THEN** that scan's `id` is used as the `scan_id` for every written trait and blob row

#### Scenario: Cross-scan image ids are rejected

- **WHEN** the RPC is called with an envelope whose `image_ids` map to more than one distinct
  `cyl_scans.id`
- **THEN** the call is rejected and nothing is written

#### Scenario: Unknown, empty, or non-numeric image ids are rejected

- **WHEN** the RPC is called with an envelope whose `image_ids` are empty, contain a non-numeric value,
  or include an id that matches no `cyl_images` row
- **THEN** the call is rejected cleanly and nothing is written

#### Scenario: A repeated image id resolving to one scan is accepted

- **WHEN** the RPC is called with an envelope whose `image_ids` contain a duplicate id, all of whose
  distinct ids belong to a single scan
- **THEN** the envelope is accepted and resolves to that one scan

### Requirement: Write-back resolves trait names through the registry

The RPC SHALL resolve each `TraitValue.name` to a `cyl_traits.id` by get-or-create (auto-register):
insert the name into the `cyl_traits` registry if absent (`ON CONFLICT (name) DO NOTHING`) and use the
resulting id, then write `cyl_scan_traits (scan_id, source_id, trait_id, value)`. It SHALL NOT depend
on a `name` column on `cyl_scan_traits` (there is none; trait identity is normalized through
`trait_id`). Re-using an already-registered trait name (within a call or across deliveries) MUST NOT
create a duplicate `cyl_traits` row. Trait-name *correctness* is a documented trust boundary: Bloom
does not re-validate names at the write boundary and relies on producer-side validation, so an unknown
name is auto-registered rather than rejected (an accepted residual registry-pollution risk).

#### Scenario: Auto-register is idempotent across deliveries

- **WHEN** a first envelope registers a trait name and a later envelope (different run) carries the
  same name
- **THEN** the later envelope reuses the existing `cyl_traits.id` and no second registry row is created

#### Scenario: A new trait name is auto-registered and linked

- **WHEN** the RPC ingests a `TraitValue` whose `name` is not yet in `cyl_traits`
- **THEN** a `cyl_traits` row for that name is created and the written `cyl_scan_traits` row references
  its `trait_id`

#### Scenario: An existing trait name is reused, not duplicated

- **WHEN** the RPC ingests a `TraitValue` whose `name` already exists in `cyl_traits`
- **THEN** the existing `cyl_traits.id` is reused and no duplicate registry row is created

### Requirement: Write-back rejects non-scan-grain traits

The RPC SHALL reject any envelope containing a `TraitValue` whose `grain` is explicitly not `"scan"`,
writing nothing, while treating an **omitted** `grain` as `"scan"` (the contract default) and
accepting it. Image-grain traits belong in `cyl_image_traits` (a separate, deferred change); silently
writing them into `cyl_scan_traits` would record an image-level measurement as a scan-level aggregate.

#### Scenario: An image-grain trait is rejected

- **WHEN** the RPC is called with an envelope containing a `TraitValue` with `grain = "image"`
- **THEN** the call is rejected and nothing is written

#### Scenario: A trait omitting grain is accepted as scan-grain

- **WHEN** the RPC is called with a `TraitValue` that omits `grain`
- **THEN** it is treated as scan-grain and written as a `cyl_scan_traits` row

### Requirement: Write-back normalizes non-finite trait values to null

For each `TraitValue`, the RPC SHALL write a finite numeric value as-is into `cyl_scan_traits.value`
and SHALL write `NULL` for any value that is JSON null, non-numeric, or non-finite. Because Postgres
accepts `'NaN'`/`'Infinity'`/`'-Infinity'` on a cast to a floating type — and because a finite value
exceeding `real` range overflows to `Infinity` on the narrowing cast — the RPC MUST apply the finite
check **on the cast result** (cast-then-check), so a non-finite value (as a number or as the string
`"NaN"`/`"Infinity"`) and an out-of-range finite value both land as SQL `NULL`.

#### Scenario: A non-finite or null trait value is stored as NULL

- **WHEN** the RPC ingests a `TraitValue` whose value is JSON null, any non-numeric string (e.g.
  `"NaN"`, `"Infinity"`, `"1.5"`, `"abc"`), or a finite number larger than the `real` column's range
- **THEN** the corresponding `cyl_scan_traits.value` is SQL `NULL`

#### Scenario: A finite trait value round-trips

- **WHEN** the RPC ingests a `TraitValue` with a finite numeric value
- **THEN** the corresponding `cyl_scan_traits.value` equals that number

### Requirement: Write-back validates envelope self-consistency and structure

The RPC SHALL reject — cleanly, writing nothing, rather than leaking a low-level error — a
structurally invalid envelope: not a JSON object; missing `provenance` or `provenance.inputs`; a
`traits` or `blobs` value that is present but not an array; a trait missing its `name`; or a blob
whose `file_size` is not an integer. It SHALL validate the envelope's one self-consistency anchor:
every `traits[].scan_key` and every `blobs[].scan_key` MUST equal `provenance.scan_key`, and a mismatch
SHALL reject the envelope. An intra-envelope duplicate — two traits resolving to the same
`(scan, source, trait)` or two blobs sharing `(kind, root_type)` — is a malformed envelope and SHALL
be rejected (symmetric handling; no silent de-duplication). A valid envelope with an empty `traits`
array and/or empty `blobs` array SHALL succeed, writing the source row and zero trait and/or blob rows.

#### Scenario: A structurally malformed envelope is rejected cleanly

- **WHEN** the RPC is called with a non-object jsonb, an envelope missing `provenance` or
  `provenance.inputs`, a non-array `traits`/`blobs`, a trait missing its `name`, or a blob with a
  non-integer `file_size`
- **THEN** the call is rejected with a clean error and nothing is written

#### Scenario: An intra-envelope duplicate is rejected

- **WHEN** the RPC is called with an envelope containing two traits that resolve to the same
  `(scan, source, trait)` or two blobs sharing `(kind, root_type)`
- **THEN** the call is rejected and nothing is written

#### Scenario: A scan_key mismatch across the envelope is rejected

- **WHEN** the RPC is called with an envelope where some `traits[].scan_key` or `blobs[].scan_key` does
  not equal `provenance.scan_key`
- **THEN** the call is rejected and nothing is written

#### Scenario: An envelope with no traits or blobs writes only the source

- **WHEN** the RPC is called with an otherwise-valid envelope whose `traits` and `blobs` arrays are
  empty
- **THEN** the source row is written and zero `cyl_scan_traits` and zero `cyl_scan_intermediates` rows
  are written

### Requirement: Write-back RPC is the sole writer of the trait tables

Direct (non-RPC) writes to the three trait tables SHALL be denied so that every write passes the
RPC's validation: writes to `cyl_trait_sources`, `cyl_scan_traits`, and `cyl_scan_intermediates` MUST
be permitted only to the RPC (via its `SECURITY DEFINER` owner) and the break-glass `bloom_admin`
(the `service_role` superuser class bypasses RLS and is out of the RLS gate by design). The migration
SHALL drop the legacy permissive `authenticated` `INSERT` policies on `cyl_trait_sources` and
`cyl_scan_traits`, and SHALL drop `bloom_writer`'s `INSERT`/`UPDATE` policies on all three tables,
while leaving `bloom_writer`'s `SELECT` access and its (and other roles') access to unrelated tables
intact. The lockdown SHALL be verified by exercising each role with `SET LOCAL ROLE` (guarded by an
assertion that those roles are not `BYPASSRLS`), because the migration/test connection role bypasses
RLS.

#### Scenario: bloom_writer cannot write the trait tables directly

- **WHEN** a session assumes `bloom_writer` and attempts a direct `INSERT` (or `UPDATE`) into
  `cyl_trait_sources`, `cyl_scan_traits`, or `cyl_scan_intermediates`
- **THEN** the write is rejected (no permitting policy)

#### Scenario: authenticated cannot insert the older trait tables directly

- **WHEN** a session assumes `authenticated` and attempts a direct `INSERT` into `cyl_trait_sources`
  or `cyl_scan_traits`
- **THEN** the write is rejected (the legacy permissive policy has been dropped)

#### Scenario: The RPC writes the same tables successfully

- **WHEN** the same data is submitted through the write-back RPC by a permitted caller assuming
  `bloom_writer`
- **THEN** the rows are written, confirming the RPC is the sanctioned write path while direct writes
  are denied

#### Scenario: bloom_writer retains read access

- **WHEN** a session assumes `bloom_writer` and runs `SELECT` against each of the three tables
- **THEN** each read is permitted

### Requirement: Intermediates blob bytes storage bucket and access control

A `cyl-intermediates` Supabase Storage bucket SHALL exist to hold the `.slp`
bytes referenced by `cyl_scan_intermediates.s3_location`. Unlike the
`cyl_scan_intermediates` TABLE (whose direct writes are restricted to
`bloom_admin` — see "Intermediates table role-based access control": all table
writes go through the write-back RPC's `SECURITY DEFINER`), there is no
RPC-mediated path for Supabase Storage byte writes, so this bucket's
`storage.objects` RLS SHALL grant `bloom_writer` and `bloom_workflows` direct
`SELECT`, `INSERT`, and `UPDATE` (scoped to `bucket_id = 'cyl-intermediates'`),
mirroring the existing `bloom_workflows`/`videos`-bucket precedent. `bloom_admin`
SHALL have `FOR ALL`; `bloom_agent` and `bloom_user` SHALL have `SELECT`-only.
No role SHALL have `DELETE`.

#### Scenario: bloom_writer can upload and read back

- **WHEN** a session assumes `bloom_writer` and uploads then reads back an
  object in the `cyl-intermediates` bucket
- **THEN** both operations succeed

#### Scenario: bloom_workflows can upload and read back

- **WHEN** a session assumes `bloom_workflows` and uploads then reads back an
  object in the `cyl-intermediates` bucket
- **THEN** both operations succeed (mirrors its existing `videos`-bucket access)

#### Scenario: Read-only roles cannot write

- **WHEN** a session assumes `bloom_agent` or `bloom_user` and attempts to
  `INSERT` or `UPDATE` an object in the `cyl-intermediates` bucket
- **THEN** the write is rejected

#### Scenario: No role can delete

- **WHEN** any non-admin role attempts to `DELETE` an object in the
  `cyl-intermediates` bucket
- **THEN** the delete is rejected

