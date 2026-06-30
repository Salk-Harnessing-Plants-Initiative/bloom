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
role model: `bloom_admin` SHALL have full access (`FOR ALL`); `bloom_agent` and `bloom_user` SHALL
have read-only (`SELECT`) access; and `bloom_writer` (the ingest/write-back role) SHALL have
`SELECT`, `INSERT`, and `UPDATE` access. The migration SHALL also issue the table-level `GRANT`s
that gate PostgREST access for those roles. The table SHALL NOT use the legacy permissive
`authenticated` policies, and SHALL NOT define any `INSERT`/`UPDATE`/`DELETE` policy for
`bloom_user` or `bloom_agent` — their read-only posture is enforced by the absence of write
policies even though the standing default table-level GRANT to `bloom_user` is permissive (so RLS,
not the GRANT, is the write gate). These guarantees are verified by exercising each role
(`SET LOCAL ROLE`) against the table, not by catalog introspection alone, because the migration/test
connection role bypasses RLS.

#### Scenario: The write-back role can write

- **WHEN** a session assumes `bloom_writer` and inserts a fully valid row (then updates it)
- **THEN** both the `INSERT` and the `UPDATE` succeed

#### Scenario: Read-only roles cannot write

- **WHEN** a session assumes `bloom_user` (and likewise `bloom_agent`) and attempts to `INSERT` a
  valid row
- **THEN** the write is rejected (insufficient privilege / no permitting policy)

#### Scenario: Every role can read

- **WHEN** a session assumes each of `bloom_admin`, `bloom_agent`, `bloom_user`, and `bloom_writer`
  and runs `SELECT` against the table
- **THEN** each read is permitted

#### Scenario: RLS is enabled with the expected policy set (drift detector)

- **WHEN** the table's RLS state and policies are introspected
- **THEN** row-level security is enabled and exactly the expected policies are present
  (`bloom_admin` all, `bloom_agent` `SELECT`, `bloom_user` `SELECT`, `bloom_writer`
  `SELECT`/`INSERT`/`UPDATE`) with no write policy for `bloom_user`/`bloom_agent`

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

