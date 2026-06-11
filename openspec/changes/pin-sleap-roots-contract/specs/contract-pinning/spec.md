## ADDED Requirements

### Requirement: Pinned contract schema is vendored at an explicit version

Bloom SHALL vendor the `sleap-roots-contracts` `result_envelope.schema.json` as a committed copy
under `contracts/schema/`, pinned at an explicit version, accompanied by a `contracts/pin.json`
manifest that records the pinned package, version, source, and the schema/generated file paths.
The vendored schema's version-stamped `$id` and the manifest's recorded version MUST agree; a
pin-consistency check SHALL fail when they diverge. The repository SHALL document, in
`contracts/README.md`, the pinned version, the re-pin procedure, and the rule that a re-pin which
only re-stamps the schema `$id` is a structural no-op (not a contract revision).

#### Scenario: Vendored schema and manifest are committed

- **WHEN** the repository is inspected
- **THEN** `contracts/schema/result_envelope.schema.json` and `contracts/pin.json` exist, and the
  manifest records the pinned package, version, and file paths

#### Scenario: Pin-consistency holds when manifest matches the schema $id

- **WHEN** the pin-consistency check runs and `pin.json.version` equals the version segment parsed
  from the vendored schema's `$id`
- **THEN** the check passes

#### Scenario: Pin-consistency fails when manifest and schema $id disagree

- **WHEN** the pin-consistency check runs and `pin.json.version` does not equal the version
  segment parsed from the vendored schema's `$id`
- **THEN** the check fails with a non-zero exit status identifying the mismatch

### Requirement: Generated TypeScript types match the pinned schema

Bloom SHALL commit TypeScript types generated from the pinned `result_envelope.schema.json`
(`ResultEnvelope`, `Provenance`, `TraitValue`, `BlobRef`, and their sub-definitions) under
`contracts/generated/`, produced by a deterministic, exact-version-pinned codegen tool. A CI drift
guard SHALL regenerate the types from the pinned schema and fail when the committed types are not
byte-identical to the regenerated output. Because the codegen does not emit the schema `$id` into
the types, a re-pin that only re-stamps the `$id` MUST regenerate byte-identical types so the
guard passes with no type change. These contract types are distinct from the Supabase
`database.types.ts` generated from the database.

#### Scenario: Drift guard passes when committed types match the pinned schema

- **WHEN** the drift guard regenerates the types from the vendored schema and compares them to the
  committed `contracts/generated/` output
- **THEN** they are byte-identical and the guard exits zero

#### Scenario: Drift guard fails when committed types diverge from the pinned schema

- **WHEN** the committed generated types differ from regenerating from the vendored schema (the
  schema changed without regenerating, or the types were hand-edited)
- **THEN** the guard exits non-zero and reports the difference

#### Scenario: A $id-only re-pin regenerates identical types

- **WHEN** the vendored schema is re-pinned to a new version whose only change is the
  version-stamped `$id` and the types are regenerated
- **THEN** the regenerated types are byte-identical to the previous committed types and the drift
  guard passes

### Requirement: Database schema matches the pinned contract

A CI check SHALL assert that Bloom's applied database schema agrees with the pinned contract for
the contract↔database mappings built today, by introspecting the live database. It SHALL assert
that the `Provenance` envelope home is `cyl_trait_sources.metadata` of type `jsonb`, and that
`Provenance.idempotency_key` (contract type `string`, default `""`) maps to
`cyl_trait_sources.idempotency_key` of type `text` guarded by a non-empty CHECK constraint. The
check SHALL be driven by a declarative mapping in which mappings introduced by later changes
(`source_id` foreign keys, the intermediates/blob table, and RPC-enforced key equality) are marked
deferred and explicitly skipped, so the check does not assert against database objects that do not
yet exist and can be extended as those changes land.

#### Scenario: Provenance envelope home is present and correctly typed

- **WHEN** the check introspects the applied database
- **THEN** `cyl_trait_sources.metadata` exists with type `jsonb`

#### Scenario: Idempotency anchor matches the contract field and its default posture

- **WHEN** the check introspects the applied database
- **THEN** `cyl_trait_sources.idempotency_key` exists with type `text` and a CHECK constraint that
  rejects the empty string, matching the contract's `idempotency_key: string` with `default: ""`

#### Scenario: Deferred mappings are skipped, not asserted

- **WHEN** the check encounters a mapping marked deferred (e.g. a `source_id` foreign key, the
  blob table, or RPC-enforced key equality not yet built)
- **THEN** that mapping is skipped with a recorded reason and does not cause a failure

#### Scenario: A regression in a built mapping fails the check

- **WHEN** an active mapping no longer holds (e.g. `cyl_trait_sources.metadata` is missing or not
  `jsonb`, or the non-empty CHECK on `idempotency_key` is absent)
- **THEN** the check fails, identifying the violated contract↔database mapping
