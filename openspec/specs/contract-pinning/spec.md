# contract-pinning Specification

## Purpose
Defines how Bloom vendors and pins the cross-language `sleap-roots-contracts`
`result_envelope.schema.json` at an explicit version: the committed schema + `pin.json` manifest, the
deterministically generated TypeScript types, the CI drift guards (pin/`$id` consistency + byte-identical
codegen), and the check that Bloom's applied database schema agrees with the pinned contract for the
mappings built so far. Keeps Bloom's consumer side in lockstep with the contract producer.
## Requirements
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
non-empty CHECK constraint and a UNIQUE constraint. It SHALL also assert that the contract
`BlobRef` maps to the `cyl_scan_intermediates` table: the table exists with the artifact-pointer
columns (`source_id`, `scan_id`, `kind`, `root_type`, `s3_location`, `box_link`, `checksum`,
`file_size`) of the expected types; the `source_id` and `scan_id` foreign keys reference
`cyl_trait_sources` and `cyl_scans` (verified by `contype`/`confrelid`); the at-least-one-location
CHECK is present (mirroring the `BlobRef` `anyOf`); and the DB `kind` vocabulary matches the pinned
contract's `BlobRef.kind` enum. The check SHALL also assert the contract-side schema facts that
justify change A's DB choices: `Provenance.contract_version` is `required` and typed `string` (the
per-row provenance-of-origin anchor), and `Provenance.idempotency_key.default` is `""` (the
documented basis for the non-empty CHECK). The check SHALL also assert the **structural** schema
support for the write-back mappings: the write-back RPC function is present in the catalog, and the
`cyl_images.scan_id → cyl_scans.id` foreign key (the `image_ids → cyl_images.scan_id` resolution path)
is present. These are structural preconditions only — the runtime *behavior* of the RPC
(`idempotency_key == metadata->>'idempotency_key'` enforcement, `contract_version` validation, and
scan resolution) is verified by the write-back RPC's own behavioral tests, not by this static check.
The check SHALL be driven by a declarative mapping in which mappings introduced by changes not yet
built (the `cyl_image_traits.source_id` foreign key) remain marked deferred and explicitly skipped, so
the check does not assert against database objects that do not yet exist and can be extended as those
changes land. Loading the contract schema SHALL NOT crash test collection when the schema file is
absent.

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

#### Scenario: BlobRef maps to the intermediates table

- **WHEN** the check introspects the applied database and the loaded contract
- **THEN** `cyl_scan_intermediates` exists with the artifact-pointer columns of the expected types,
  foreign keys from `source_id` to `cyl_trait_sources` and `scan_id` to `cyl_scans` (by
  `contype`/`confrelid`), an at-least-one-location CHECK, and a `kind` vocabulary equal to the
  pinned contract's `BlobRef.kind` enum

#### Scenario: Contract-side anchors that justify the DB schema are present

- **WHEN** the check inspects the loaded contract
- **THEN** `Provenance.contract_version` is in `required` and typed `string`, and
  `Provenance.idempotency_key.default` is `""`, and the check fails if a re-pin drops either (the
  provenance anchor and the empty-string CHECK rationale must stay guaranteed by the contract)

#### Scenario: Write-back mappings are active and their structural support is asserted

- **WHEN** the check introspects the applied database
- **THEN** the write-back RPC function is present in the catalog and the
  `cyl_images.scan_id → cyl_scans.id` foreign key (the `image_ids → cyl_images.scan_id` resolution
  path) is present, and these mappings are no longer marked deferred (their runtime enforcement is
  covered by the write-back RPC behavioral tests, not this static check)

#### Scenario: Deferred mappings are skipped, not asserted

- **WHEN** the check encounters a mapping marked deferred (the `cyl_image_traits.source_id` foreign
  key, not yet built)
- **THEN** that mapping is skipped with a recorded reason and does not cause a failure

#### Scenario: A regression in a built mapping fails the check

- **WHEN** an active mapping no longer holds (e.g. `cyl_trait_sources.metadata` is missing or not
  `jsonb`, the contract no longer designates that home, the non-empty CHECK or the UNIQUE
  constraint on `idempotency_key` is absent, `cyl_scan_intermediates` is missing a mapped
  column, foreign key, or the at-least-one-location CHECK, the write-back RPC function is absent, or
  the `cyl_images.scan_id → cyl_scans.id` foreign key is absent)
- **THEN** the check fails, identifying the violated contract↔database mapping

#### Scenario: Missing contract schema does not crash test collection

- **WHEN** the test module is collected and the vendored contract schema file is absent (module-level
  state references only literals, so collection does not read the schema)
- **THEN** the affected tests are skipped at execution rather than raising and failing collection for
  the whole integration suite

