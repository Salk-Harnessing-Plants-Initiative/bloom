## ADDED Requirements

### Requirement: Write-back RPC ingests a ResultEnvelope

Bloom SHALL provide an in-database `SECURITY DEFINER` function (the write-back RPC, callable via
PostgREST) that takes one contract `ResultEnvelope` as `jsonb` and, in a **single transaction**,
writes it into `cyl_trait_sources`, `cyl_scan_traits` (via the `cyl_traits` registry), and
`cyl_scan_intermediates`. The function SHALL pin its owner deterministically and harden its execution
environment (`SET search_path` to a fixed safe value; schema-qualified writes; parameterized value
binding, never string-interpolated SQL). It MUST NOT be executable by `PUBLIC`; `EXECUTE` SHALL be
granted only to `bloom_writer`, `service_role`, and `bloom_admin`. The RPC performs, in order:
(1) structural + contract-version validation; (2) idempotency-key validation; (3) scan resolution;
(4) the source upsert; (5) trait-name resolution and trait writes; (6) blob writes. Any validation or
constraint failure SHALL abort the entire call so that no partial source, trait, registry, or blob
rows persist (all-or-nothing). The RPC SHALL return a `jsonb` summary reporting the source id, the
resolved scan id (null on a no-op re-delivery), the trait and blob counts (equal to rows written), and
whether the call was a no-op re-delivery.

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
- **THEN** `PUBLIC` cannot execute it and exactly `bloom_writer`, `service_role`, and `bloom_admin`
  hold `EXECUTE`

#### Scenario: The definer can write after the lockdown (owner and FORCE RLS guard)

- **WHEN** the function's catalog metadata is introspected
- **THEN** it is `SECURITY DEFINER` with a pinned `search_path`, is owned by a role that can write all
  three tables under the post-lockdown policies, and none of the three tables has `FORCE ROW LEVEL
  SECURITY` enabled (which would re-subject the owner to RLS and break the only write path)

### Requirement: Write-back is idempotent and provenance-immutable

The RPC SHALL use the `cyl_trait_sources` insert as an atomic gate: `ON CONFLICT (idempotency_key)
DO NOTHING RETURNING id`. When a row is returned the call created the source and SHALL proceed to
write its traits and blobs; when no row is returned the run was already ingested and the RPC SHALL
**short-circuit to a pure no-op** — writing no further source, trait, blob, or registry rows — and
report the no-op. One run maps to exactly one source row, and re-delivery of an already-ingested run
changes nothing (source, traits, and blobs are immutable; the stored `metadata`/provenance is never
overwritten, even if a re-delivered envelope carries a divergent `metadata`, since volatile provenance
fields are not in the producer's key hash). Because the whole ingest is one transaction, the source
row exists only if a prior delivery fully committed, so a partial/failed delivery leaves nothing and a
retry writes the full envelope.

#### Scenario: Re-delivery of the same envelope is a pure no-op

- **WHEN** the RPC is called twice with the same `ResultEnvelope`
- **THEN** exactly one `cyl_trait_sources` row exists, there are no duplicate `cyl_scan_traits` or
  `cyl_scan_intermediates` rows, and the second call reports a no-op

#### Scenario: Re-delivery with divergent metadata does not overwrite stored provenance

- **WHEN** the RPC is called again with the same `idempotency_key` but a different `metadata` payload
- **THEN** the originally stored `cyl_trait_sources.metadata` is unchanged and no further rows are
  written

#### Scenario: Re-delivery resolving to a different scan writes nothing to that scan

- **WHEN** the RPC is called again with the same `idempotency_key` but `image_ids` that resolve to a
  different scan than the original delivery
- **THEN** the call short-circuits on the existing source and writes no `cyl_scan_traits` or
  `cyl_scan_intermediates` rows against the different scan (the run identity, not the scan, governs)

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

The RPC SHALL validate that `provenance.contract_version` equals the contract version Bloom is pinned
to (`v0.1.0a2`), and SHALL reject any envelope whose `contract_version` does not match, writing
nothing. This anchors every written row to a known contract-of-origin.

#### Scenario: Matching contract version is accepted

- **WHEN** the RPC is called with `provenance.contract_version` equal to the pinned version
- **THEN** the envelope is ingested

#### Scenario: Mismatched contract version is rejected

- **WHEN** the RPC is called with `provenance.contract_version` not equal to the pinned version
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

## MODIFIED Requirements

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
