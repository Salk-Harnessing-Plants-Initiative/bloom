## MODIFIED Requirements

### Requirement: Pinned contract schema is vendored at an explicit version

Bloom SHALL vendor the `sleap-roots-contracts` `result_envelope.schema.json` as a committed, LF-normalized copy under `contracts/schema/`, pinned at an explicit version, accompanied by a `contracts/pin.json` manifest that records the pinned package, version, the full schema `$id` string, source, and the schema/generated file paths. A pin-consistency check SHALL fail unless the manifest's recorded `$id` exactly equals the vendored schema's `$id` AND the manifest's recorded version equals the version segment parsed from that `$id` (parsed with an anchored rule matching `…/schema/<version>/result_envelope.schema.json`). A missing or unparseable `$id` SHALL fail the check. The repository SHALL document, in `contracts/README.md`, the pinned version, the re-pin procedure, and the rule that a re-pin which only re-stamps the schema `$id` is a structural no-op (not a contract revision). The documented re-pin procedure SHALL additionally instruct that, for any re-pin whose migration adds a data-dependent cutover guard (a `DO` block that raises if real historical data would be silently orphaned — the pattern established by `supabase/migrations/20260706170000_cyl_writeback_contract_a3.sql` and `supabase/migrations/20260831130000_cyl_writeback_contract_a7.sql`), the author MUST check the real state of every target environment the guard could plausibly trip in (at minimum staging; production too if the guarded condition could exist there) before merging, and MUST fold any reconciliation the check turns up into the same PR. This check SHALL be documented as a manual step performed via SSH to the deploy host (mirroring `scripts/deploy_run_supabase.sh` / the in-container `psql` pattern `deploy.yml` already uses for schema grants) — regular pull request CI has no route to staging/production secrets (they are scoped behind `environment: staging`/`environment: production`, reachable only from a `push` to `staging`/`main` or an explicit `workflow_dispatch`, never a `pull_request` event) — and SHALL NOT be described as an automated CI gate that blocks a PR from merging.

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

#### Scenario: An author of a cutover-guard re-pin checks real staging state before merging

- **GIVEN** a developer is writing a re-pin migration with a cutover guard following the `repin-cyl-contract-a3`/`repin-cyl-contract-a7` pattern
- **WHEN** they consult `contracts/README.md`'s documented re-pin procedure
- **THEN** it instructs them to query the real staging database (via SSH + the deploy host's existing tooling) for rows that would trip the guard, before opening or merging the PR
- **AND** to fold any needed reconciliation into the same PR if the query finds blocking rows

#### Scenario: The documentation does not claim this is enforced by CI

- **WHEN** the documented procedure is read
- **THEN** it states plainly that no automated pre-merge CI check performs this query today, and explains why (PR CI has no access to staging/production deploy secrets)
- **AND** it does not describe the check as something that blocks a PR from merging
