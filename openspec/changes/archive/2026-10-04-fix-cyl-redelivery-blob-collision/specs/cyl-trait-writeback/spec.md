## ADDED Requirements

### Requirement: Trait source idempotency key is readable by the write-back identity

`bloom_workflows` SHALL hold column-scoped `SELECT (idempotency_key)` on
`public.cyl_trait_sources`, so that `bloomctl` can determine whether a delivery has already been
ingested before uploading its blobs. Postgres requires `SELECT` on every column a query
references, including those in a `WHERE` clause, so this grant is what permits filtering on
`idempotency_key`; it exists to let that filter use the existing
`cyl_trait_sources_idempotency_key_key` UNIQUE index rather than a `metadata->>` expression.

This grant SHALL widen no information the role can already reach: `SELECT (id, metadata)` on the
same table, together with the `workflows_read_cyl_trait_sources` RLS policy, is already granted
(`20260730120000_create_cyl_pipeline_runs.sql:167-169` and `:172`), and the RPC stores `metadata`
as the envelope's `provenance` object, which itself contains `idempotency_key`. The grant adds an
indexed access path to a value the role can already read, not a new capability.

The grant SHALL be additive only, and SHALL remain column-scoped. No `INSERT`, `UPDATE`, or
`DELETE` privilege is added on `cyl_trait_sources`; no column-less `GRANT SELECT` on the table is
introduced, since that would silently reach every column; and the execute-only posture on the
write-back RPC (`20260720000000_grant_bloom_workflows_writeback_rpc.sql`) is unchanged.

A paired rollback SHALL revoke only this column. A bare `REVOKE SELECT` would strip the
pre-existing `(id, metadata)` grant that the dedup-preview read path depends on.

#### Scenario: bloom_workflows can filter on the key

- **WHEN** a session with `SET LOCAL ROLE bloom_workflows` selects `id` from
  `cyl_trait_sources` filtered on `idempotency_key`
- **THEN** the query succeeds, returning the matching row for a seeded key and zero rows for an
  absent one

#### Scenario: The grant confers no write access

- **WHEN** `bloom_workflows` attempts an `INSERT`, `UPDATE`, or `DELETE` on
  `cyl_trait_sources`
- **THEN** the statement is refused, exactly as before this grant

#### Scenario: Other columns remain ungranted

- **WHEN** `bloom_workflows` selects a `cyl_trait_sources` column other than `id`,
  `metadata`, or `idempotency_key`
- **THEN** the statement is refused with a permission error

#### Scenario: The rollback leaves the pre-existing grant intact

- **WHEN** the paired rollback is applied
- **THEN** `SELECT (idempotency_key)` is revoked from `bloom_workflows` while
  `SELECT (id, metadata)` remains granted
