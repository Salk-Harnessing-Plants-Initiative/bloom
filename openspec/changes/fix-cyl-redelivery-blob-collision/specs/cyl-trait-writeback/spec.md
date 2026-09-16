## ADDED Requirements

### Requirement: The write-back identity can read cyl_trait_sources.idempotency_key

`bloom_workflows` SHALL hold column-scoped `SELECT (idempotency_key)` on
`public.cyl_trait_sources`, so that `bloomctl` can determine whether a delivery has
already been ingested before uploading its blobs, using the existing
`cyl_trait_sources_idempotency_key_key` UNIQUE index.

This grant SHALL widen no information the role can already reach: `SELECT (id, metadata)`
on the same table, together with the `workflows_read_cyl_trait_sources` RLS policy, is
already granted (`20260730120000_create_cyl_pipeline_runs.sql:168-172`), and the RPC
stores `metadata` as the envelope's `provenance` object, which itself contains
`idempotency_key`. The grant adds an indexed access path to a value the role can already
read, not a new capability.

The grant SHALL be additive only. No `INSERT`, `UPDATE`, or `DELETE` privilege is added
on `cyl_trait_sources`, and the execute-only posture on the write-back RPC
(`20260720000000_grant_bloom_workflows_writeback_rpc.sql`) is unchanged.

#### Scenario: bloom_workflows reads the key through the unique index

- **WHEN** `bloomctl`, authenticated as `bloom_workflows`, selects `id` from
  `cyl_trait_sources` filtered on `idempotency_key`
- **THEN** the query succeeds and is served by
  `cyl_trait_sources_idempotency_key_key`

#### Scenario: The grant confers no write access

- **WHEN** `bloom_workflows` attempts an `INSERT`, `UPDATE`, or `DELETE` on
  `cyl_trait_sources`
- **THEN** the statement is refused, exactly as before this grant

#### Scenario: Other columns remain ungranted

- **WHEN** `bloom_workflows` selects a `cyl_trait_sources` column other than `id`,
  `metadata`, or `idempotency_key`
- **THEN** the statement is refused
