## ADDED Requirements

### Requirement: Pinned contract schema is vendored at an explicit version

Bloom SHALL vendor the `sleap-roots-contracts` `result_envelope.schema.json` as a committed,
LF-normalized copy under `contracts/schema/`, pinned at an explicit version, accompanied by a
`contracts/pin.json` manifest that records the pinned package, version, the full schema `$id`
string, source, and the schema/generated file paths. A pin-consistency check SHALL fail unless
the manifest's recorded `$id` exactly equals the vendored schema's `$id` AND the manifest's
recorded version equals the version segment parsed from that `$id` (parsed with an anchored rule
matching `…/schema/<version>/result_envelope.schema.json`). A missing or unparseable `$id` SHALL
fail the check. The repository SHALL document, in `contracts/README.md`, the pinned version, the
re-pin procedure, and the rule that a re-pin which only re-stamps the schema `$id` is a structural
no-op (not a contract revision).

#### Scenario: Vendored schema and manifest are committed

- **WHEN** the repository is inspected
- **THEN** `contracts/schema/result_envelope.schema.json` and `contracts/pin.json` exist, and the
  manifest records the pinned package, version, full `$id`, and file paths

#### Scenario: Pin-consistency holds when manifest matches the schema $id

- **WHEN** the pin-consistency check runs and both `pin.json.id` equals the vendored schema's
  `$id` and `pin.json.version` equals the version segment parsed from that `$id`
- **THEN** the check passes

#### Scenario: Pin-consistency fails when manifest and schema $id disagree

- **WHEN** the pin-consistency check runs and `pin.json.id`/`pin.json.version` does not match the
  vendored schema's `$id`
- **THEN** the check fails with a non-zero exit status identifying the mismatch

#### Scenario: Pin-consistency fails when the schema $id is missing or unparseable

- **WHEN** the pin-consistency check runs against a schema whose `$id` is absent or does not match
  the expected `…/schema/<version>/result_envelope.schema.json` shape
- **THEN** the check fails with a non-zero exit status rather than silently passing

#### Scenario: Pinned schema retains the contract_version traceability anchor

- **WHEN** the contract-sanity check runs against the pinned schema
- **THEN** it confirms `contract_version` is a required `string` field of `Provenance`, and fails
  if a re-pin ever drops it from `required` (the per-row provenance-of-origin anchor must be
  guaranteed by every envelope)

### Requirement: Generated TypeScript types match the pinned schema

Bloom SHALL commit TypeScript types generated from the pinned `result_envelope.schema.json`
(`ResultEnvelope`, `Provenance`, `TraitValue`, `BlobRef`, and their sub-definitions) under
`contracts/generated/`, produced by a deterministic, exact-version-pinned codegen tool. The
generated file SHALL be excluded from repository prettier formatting and pinned to LF line endings
so the codegen tool's output is the single authority over its bytes across operating systems. A CI
drift guard SHALL regenerate the types from the pinned schema, normalize line endings, and fail
when the committed types are not byte-identical to the regenerated output. Because the codegen does
not emit the schema `$id` into the types, a re-pin that only re-stamps the `$id` MUST regenerate
byte-identical types so the guard passes with no type change; conversely, a change to any contract
field MUST produce a different generated output so the guard fails. The committed types SHALL be
valid TypeScript (type-checkable). These contract types are distinct from the Supabase
`database.types.ts` generated from the database.

#### Scenario: Drift guard passes when committed types match the pinned schema

- **WHEN** the drift guard regenerates the types from the vendored schema and compares them
  (line-ending-normalized) to the committed `contracts/generated/` output
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

#### Scenario: A real contract field change fails the guard

- **WHEN** a contract field changes (a property is added/removed or a type changes) in addition to
  or instead of the `$id`, and the types are regenerated
- **THEN** the regenerated types differ from the committed types and the drift guard exits non-zero

#### Scenario: Generated types are valid TypeScript

- **WHEN** the committed `contracts/generated/` types are type-checked
- **THEN** they compile without type errors

### Requirement: Database schema matches the pinned contract

A CI check SHALL assert that Bloom's applied database schema agrees with the pinned contract for
the contract↔database mappings built today, by introspecting the live database. It SHALL assert
that the `Provenance` envelope home is `cyl_trait_sources.metadata` of type `jsonb`; that the
loaded contract still designates that home (the schema's `Provenance` description names
`cyl_trait_sources.metadata`); and that `Provenance.idempotency_key` (contract type `string`,
default `""`) maps to `cyl_trait_sources.idempotency_key` of type `text` guarded by **both** a
non-empty CHECK constraint and a UNIQUE constraint. The check SHALL be driven by a declarative
mapping in which mappings introduced by later changes (`source_id` foreign keys, the
intermediates/blob table, RPC-enforced key equality, `contract_version` row-anchor validation, and
`scan_key` resolution) are marked deferred and explicitly skipped, so the check does not assert
against database objects that do not yet exist and can be extended as those changes land. Loading
the contract schema SHALL NOT crash test collection when the schema file is absent.

Note: this requirement may relocate to the `cyl-trait-writeback` capability in a future refactor.

#### Scenario: Provenance envelope home is present, correctly typed, and contract-designated

- **WHEN** the check introspects the applied database and the loaded contract
- **THEN** `cyl_trait_sources.metadata` exists with type `jsonb`, and the contract's `Provenance`
  description still designates `cyl_trait_sources.metadata` as the home

#### Scenario: Idempotency anchor matches the contract field, default posture, and uniqueness

- **WHEN** the check introspects the applied database
- **THEN** `cyl_trait_sources.idempotency_key` exists with type `text`, a constraint of type CHECK
  (`contype = 'c'`) that rejects the empty string (matching the contract's `idempotency_key:
  string` with `default: ""`), and a constraint of type UNIQUE (`contype = 'u'`, the
  `1 ResultEnvelope : 1 source row` anchor) — verified by constraint *type*, not name alone

#### Scenario: Deferred mappings are skipped, not asserted

- **WHEN** the check encounters a mapping marked deferred (e.g. a `source_id` foreign key, the
  blob table, RPC-enforced key equality, `contract_version` validation, or `scan_key` resolution
  not yet built)
- **THEN** that mapping is skipped with a recorded reason and does not cause a failure

#### Scenario: A regression in a built mapping fails the check

- **WHEN** an active mapping no longer holds (e.g. `cyl_trait_sources.metadata` is missing or not
  `jsonb`, the contract no longer designates that home, or the non-empty CHECK or the UNIQUE
  constraint on `idempotency_key` is absent)
- **THEN** the check fails, identifying the violated contract↔database mapping

#### Scenario: Missing contract schema does not crash test collection

- **WHEN** the test module is collected and the vendored contract schema file is absent
- **THEN** the test is skipped (module-level) rather than raising and failing collection for the
  whole integration suite
