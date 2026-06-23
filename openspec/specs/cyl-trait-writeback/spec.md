# cyl-trait-writeback Specification

## Purpose
TBD - created by archiving change add-cyl-trait-source-provenance. Update Purpose after archive.
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

