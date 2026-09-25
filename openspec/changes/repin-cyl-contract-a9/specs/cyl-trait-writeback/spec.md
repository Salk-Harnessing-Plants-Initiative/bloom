## MODIFIED Requirements

### Requirement: Write-back validates the contract version

The RPC SHALL validate that `provenance.contract_version` matches the contract version Bloom is
pinned to (`0.1.0a9`), and SHALL reject any envelope whose `contract_version` does not match, writing
nothing. This anchors every written row to a known contract-of-origin. The match SHALL be
**prefix-tolerant**: a **single lowercase leading `v`** is normalized away on both the incoming value
and the pinned version before comparison, so the bare package-version form the emitter stamps
(`0.1.0a9`, read from the installed `sleap-roots-contracts` distribution) and the `v`-prefixed
git-tag/`$id` form (`v0.1.0a9`) are both accepted. Normalization is scoped to that single lowercase
`v`: an uppercase `V`, a doubled `vv`, surrounding whitespace, or any build/local segment is NOT the
pinned version and SHALL be rejected. An absent or empty `contract_version` SHALL be rejected (the
comparison operates on the coalesced normalized strings, so a `NULL`/absent value collapses to the
empty string and fails the match rather than passing). Only the single pinned version (in either
accepted form) is accepted — any other version, including any previously pinned version
(`0.1.0a7`/`v0.1.0a7`, `0.1.0a3`/`v0.1.0a3`, `0.1.0a2`/`v0.1.0a2`), SHALL be rejected.

#### Scenario: Matching bare contract version is accepted

- **WHEN** the RPC is called with `provenance.contract_version` equal to the pinned version in its
  bare package form (`0.1.0a9`)
- **THEN** the envelope is ingested

#### Scenario: Matching v-prefixed contract version is accepted

- **WHEN** the RPC is called with `provenance.contract_version` equal to the pinned version with a
  single lowercase leading `v` (`v0.1.0a9`)
- **THEN** the envelope is ingested, the leading `v` having been normalized away before comparison

#### Scenario: A previously pinned contract version is rejected

- **WHEN** the RPC is called with `provenance.contract_version` set to a previously pinned version
  in either form (`0.1.0a7`/`v0.1.0a7`, `0.1.0a3`/`v0.1.0a3`, or `0.1.0a2`/`v0.1.0a2`)
- **THEN** the call is rejected with a `contract_version mismatch` error and nothing is written (each
  re-pin is a hard cutover, not a compatibility set)

#### Scenario: A non-pinned or malformed version form is rejected

- **WHEN** the RPC is called with `provenance.contract_version` set to any other value — an unrelated
  or never-pinned version (such as `0.1.0a8`), an uppercase `V0.1.0a9`, a doubled `vv0.1.0a9`, a
  trailing-whitespace `0.1.0a9 `, or a near-miss `0.1.0a90`
- **THEN** the call is rejected and nothing is written

#### Scenario: Absent or empty contract version is rejected

- **WHEN** the RPC is called with `provenance.contract_version` absent or set to the empty string
- **THEN** the call is rejected and nothing is written

## ADDED Requirements

### Requirement: A contract re-pin leaves existing rows untouched

A migration that re-pins the RPC's accepted `contract_version` SHALL NOT refuse to apply solely
because `cyl_trait_sources` rows stamped with a previously pinned version exist, and SHALL NOT
rewrite, restamp, or delete those rows as part of the re-pin. The pin gates new inserts only: each
existing row keeps its own `metadata->>'contract_version'` as truthful provenance of the contract it
was written under, and its trait and blob rows remain readable exactly as before. (This supersedes the
retiring-version cutover guards in the `0.1.0a3` and `0.1.0a7` re-pin migrations, which raised on
exactly such rows.)

#### Scenario: A re-pin applies over rows stamped with the previous pin

- **GIVEN** a `cyl_trait_sources` row written through the RPC while the previous version was pinned,
  carrying that version as its `contract_version`, with trait and blob rows
- **WHEN** the re-pin migration is applied
- **THEN** the migration succeeds without raising
- **AND** that row's `metadata` (including its `contract_version`), its `cyl_scan_traits` rows and its
  `cyl_scan_intermediates` rows are unchanged, and its trait values are still returned by the
  source-aware read path

#### Scenario: A redelivered previous-version envelope is rejected, not rewritten

- **GIVEN** the re-pin is applied and a row stamped with the previous version exists for idempotency
  key `K`
- **WHEN** the RPC is called with a previous-version envelope carrying the same key `K`
- **THEN** the call is rejected with a `contract_version mismatch` error, before the source gate
  (so it is not reported as an idempotent no-op)
- **AND** the existing row is unchanged
