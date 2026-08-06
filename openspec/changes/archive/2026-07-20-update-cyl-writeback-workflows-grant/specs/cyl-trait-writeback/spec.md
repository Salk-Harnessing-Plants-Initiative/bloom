## MODIFIED Requirements

### Requirement: Write-back RPC ingests a ResultEnvelope

Bloom SHALL provide an in-database `SECURITY DEFINER` function (the write-back RPC, callable via
PostgREST) that takes one contract `ResultEnvelope` as `jsonb` and, in a **single transaction**,
writes it into `cyl_trait_sources`, `cyl_scan_traits` (via the `cyl_traits` registry), and
`cyl_scan_intermediates`. The function SHALL pin its owner deterministically and harden its execution
environment (`SET search_path` to a fixed safe value; schema-qualified writes; parameterized value
binding, never string-interpolated SQL). It MUST NOT be executable by `PUBLIC`; `EXECUTE` SHALL be
granted only to `bloom_writer`, `service_role`, `bloom_admin`, and `bloom_workflows` (the scoped,
non-interactive service identity used by cluster write-back pods). The RPC performs, in order:
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
