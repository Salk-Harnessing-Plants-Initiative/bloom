## MODIFIED Requirements

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
documented basis for the non-empty CHECK). The check SHALL be driven by a declarative mapping in
which mappings introduced by later changes (RPC-enforced key equality, `contract_version`
row-anchor validation, and `scan_key` resolution) are marked deferred and explicitly skipped, so
the check does not assert against database objects that do not yet exist and can be extended as
those changes land. Loading the contract schema SHALL NOT crash test collection when the schema
file is absent.

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

#### Scenario: Deferred mappings are skipped, not asserted

- **WHEN** the check encounters a mapping marked deferred (e.g. RPC-enforced key equality,
  `contract_version` validation, or `scan_key` resolution not yet built)
- **THEN** that mapping is skipped with a recorded reason and does not cause a failure

#### Scenario: A regression in a built mapping fails the check

- **WHEN** an active mapping no longer holds (e.g. `cyl_trait_sources.metadata` is missing or not
  `jsonb`, the contract no longer designates that home, the non-empty CHECK or the UNIQUE
  constraint on `idempotency_key` is absent, or `cyl_scan_intermediates` is missing a mapped
  column, foreign key, or the at-least-one-location CHECK)
- **THEN** the check fails, identifying the violated contract↔database mapping

#### Scenario: Missing contract schema does not crash test collection

- **WHEN** the test module is collected and the vendored contract schema file is absent (module-level
  state references only literals, so collection does not read the schema)
- **THEN** the affected tests are skipped at execution rather than raising and failing collection for
  the whole integration suite
