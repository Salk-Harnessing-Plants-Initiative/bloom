## MODIFIED Requirements

### Requirement: Write-back validates the contract version

The RPC SHALL validate that `provenance.contract_version` matches the contract version Bloom is
pinned to (`0.1.0a3`), and SHALL reject any envelope whose `contract_version` does not match, writing
nothing. This anchors every written row to a known contract-of-origin. The match SHALL be
**prefix-tolerant**: a **single lowercase leading `v`** is normalized away on both the incoming value
and the pinned version before comparison, so the bare package-version form the emitter stamps
(`0.1.0a3`, read from the installed `sleap-roots-contracts` distribution) and the `v`-prefixed
git-tag/`$id` form (`v0.1.0a3`) are both accepted. Normalization is scoped to that single lowercase
`v`: an uppercase `V`, a doubled `vv`, surrounding whitespace, or any build/local segment is NOT the
pinned version and SHALL be rejected. An absent or empty `contract_version` SHALL be rejected (the
comparison operates on the coalesced normalized strings, so a `NULL`/absent value collapses to the
empty string and fails the match rather than passing). Only the single pinned version (in either
accepted form) is accepted — any other version, including the previously pinned `0.1.0a2`/`v0.1.0a2`,
SHALL be rejected.

#### Scenario: Matching bare contract version is accepted

- **WHEN** the RPC is called with `provenance.contract_version` equal to the pinned version in its
  bare package form (`0.1.0a3`)
- **THEN** the envelope is ingested

#### Scenario: Matching v-prefixed contract version is accepted

- **WHEN** the RPC is called with `provenance.contract_version` equal to the pinned version with a
  single lowercase leading `v` (`v0.1.0a3`)
- **THEN** the envelope is ingested, the leading `v` having been normalized away before comparison

#### Scenario: The previously pinned contract version is rejected

- **WHEN** the RPC is called with `provenance.contract_version` set to the previously pinned version
  in either form (`0.1.0a2` or `v0.1.0a2`)
- **THEN** the call is rejected and nothing is written (the re-pin is a hard cutover, not a
  compatibility set)

#### Scenario: A non-pinned or malformed version form is rejected

- **WHEN** the RPC is called with `provenance.contract_version` set to any other value — an unrelated
  version, an uppercase `V0.1.0a3`, a doubled `vv0.1.0a3`, a trailing-whitespace `0.1.0a3 `, or a
  near-miss `0.1.0a30`
- **THEN** the call is rejected and nothing is written

#### Scenario: Absent or empty contract version is rejected

- **WHEN** the RPC is called with `provenance.contract_version` absent or set to the empty string
- **THEN** the call is rejected and nothing is written
