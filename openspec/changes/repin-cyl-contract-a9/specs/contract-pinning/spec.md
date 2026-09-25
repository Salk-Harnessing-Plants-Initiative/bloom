## MODIFIED Requirements

### Requirement: Pinned contract schema is vendored at an explicit version

Bloom SHALL vendor the `sleap-roots-contracts` `result_envelope.schema.json` as a committed, LF-normalized copy under `contracts/schema/`, pinned at an explicit version, accompanied by a `contracts/pin.json` manifest that records the pinned package, version, the full schema `$id` string, source, and the schema/generated file paths. A pin-consistency check SHALL fail unless the manifest's recorded `$id` exactly equals the vendored schema's `$id` AND the manifest's recorded version equals the version segment parsed from that `$id` (parsed with an anchored rule matching `…/schema/<version>/result_envelope.schema.json`). A missing or unparseable `$id` SHALL fail the check. The repository SHALL document, in `contracts/README.md`, the pinned version, the re-pin procedure, and the rule that a re-pin which only re-stamps the schema `$id` is a structural no-op (not a contract revision). The documented re-pin procedure SHALL instruct that every re-pin also re-pins the write-back RPC's accepted `contract_version` literal in a new forward migration, in the same change, and that a re-pin migration SHALL NOT add a guard that raises because rows stamped with the retiring version exist (see `cyl-trait-writeback`, "A contract re-pin leaves existing rows untouched"; the retiring-version guards in `supabase/migrations/20260706170000_cyl_writeback_contract_a3.sql` and `supabase/migrations/20260831130000_cyl_writeback_contract_a7.sql` are historical and SHALL NOT be copied). The documented procedure SHALL additionally instruct that, for any re-pin whose migration's success or effect depends on existing data (for example, a real contract revision that needs a backfill), the author MUST check the real state of staging before merging, and MUST additionally check production unless the author can positively confirm from schema/migration history that the relevant condition cannot exist there — an unverified "probably fine" SHALL NOT be treated as sufficient grounds to skip the production check — and MUST fold any reconciliation the check turns up into the same PR. This check SHALL be documented as a manual step performed via SSH to the deploy host (mirroring `scripts/deploy_run_supabase.sh` / the in-container `psql` pattern `deploy.yml` already uses for schema grants) — regular pull request CI has no route to staging/production secrets (they are scoped behind `environment: staging`/`environment: production`, reachable only from a `push` to `staging`/`main` or an explicit `workflow_dispatch`, never a `pull_request` event) — and SHALL NOT be described as an automated CI gate that blocks a PR from merging.

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

#### Scenario: The re-pin procedure forbids a retiring-version guard

- **GIVEN** a developer is writing a re-pin migration
- **WHEN** they consult `contracts/README.md`'s documented re-pin procedure
- **THEN** it instructs them to re-pin the RPC literal in a new forward migration in the same change
- **AND** it tells them not to add a guard that raises on rows stamped with the retiring version, and
  explains why (the `0.1.0a7` guard wedged staging deploys on legitimate rows, bloom#685, cleared only
  by the bloom#787 restamp)

#### Scenario: An author of a data-dependent re-pin checks real staging state before merging

- **GIVEN** a developer is writing a re-pin migration whose success or effect depends on existing data
- **WHEN** they consult `contracts/README.md`'s documented re-pin procedure
- **THEN** it instructs them to query the real staging database (via SSH + the deploy host's existing tooling) for the relevant rows, before opening or merging the PR
- **AND** to fold any needed reconciliation into the same PR if the query finds blocking rows

#### Scenario: The documentation does not claim this is enforced by CI

- **WHEN** the documented procedure is read
- **THEN** it states plainly that no automated pre-merge CI check performs this query today, and explains why (PR CI has no access to staging/production deploy secrets)
- **AND** it does not describe the check as something that blocks a PR from merging

## ADDED Requirements

### Requirement: The write-back RPC's pinned version matches the vendored pin

A CI check SHALL fail unless the `pinned_version` literal in the applied definition of the 2-arg
`insert_cyl_result_envelope(jsonb, text)` equals `contracts/pin.json`'s `version` with a single
leading lowercase `v` removed, so the vendored contract and the RPC it describes cannot drift apart
(the RPC stayed pinned to `0.1.0a3` while the vendored pin had moved to `v0.1.0a5`, until
`repin-cyl-contract-a7`). The check SHALL read the literal from the live catalog
(`pg_get_functiondef`), not from a migration file.

#### Scenario: Pin and RPC literal agree

- **WHEN** the check runs against a database whose `insert_cyl_result_envelope(jsonb, text)` pins
  the same version `contracts/pin.json` records
- **THEN** it passes

#### Scenario: Pin and RPC literal disagree

- **WHEN** the check runs and the RPC's `pinned_version` differs from `contracts/pin.json`'s version
- **THEN** it fails, naming both values
