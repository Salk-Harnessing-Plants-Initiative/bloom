> **Commit atomicity (read first).** All of §1–§3 co-land in a single commit/PR. The read-path suite
> (§3.1) seeds via the RPC, so once the a3 migration (§2.2) applies, any commit that lacks the §3.1
> `PINNED_VERSION` flip is red. CI applies migrations from the branch tree then runs the *entire*
> `tests/integration/` dir in one job, so RED-before-GREEN is a **local, developer-machine** discipline
> (run against an un-migrated DB), not a CI-observable per-step state. No intermediate commit is
> expected to be green.

## 1. Re-pin the vendored contract (`v0.1.0a2` → `v0.1.0a3`)

- [x] 1.1 Canonically diff the published `a3` schema against the vendored `a2` `$defs` (Provenance, BlobRef) — verified the payload delta is **exactly** the two new optional nullable `Provenance` fields (`predict_inference_config`, `predict_output_params`) + the `$id` restamp: no removed fields, `required` byte-identical, `BlobRef` untouched
- [x] 1.2 Confirmed the `a3` schema preserves the load-bearing `Provenance.description` string `cyl_trait_sources.metadata`
- [x] 1.3 Replaced `contracts/schema/result_envelope.schema.json` with the published `v0.1.0a3` copy (LF, trailing newline preserved; no prettier from inside `contracts/`)
- [x] 1.4 Bumped `contracts/pin.json` `version`/`id`/`source` to `v0.1.0a3`
- [x] 1.5 Regenerated `contracts/generated/result-envelope.ts` (`npm run contracts:gen`); diff is exactly the two new optional `Provenance` fields + their type aliases (8 insertions), nothing else
- [x] 1.6 Updated `contracts/README.md` — "Currently pinned: `v0.1.0a3`" + a re-pin note (real additive revision; rides in opaque `metadata`; links the companion `sleap-roots-contracts` convention issue placeholder)
- [x] 1.7 Drift guards pass: `npm run contracts:check` (types agree) and `npm run contracts:test` (9/9)

## 2. Write-back RPC accepts `0.1.0a3`, prefix-tolerant (TDD)

- [x] 2.1 (RED) Added `drop_contract_version` flag to `_envelope()`; set `PINNED_VERSION = "0.1.0a3"`; added named tests: `test_bare_contract_version_accepted`, `test_v_prefixed_contract_version_accepted`, `test_a2_contract_version_rejected[0.1.0a2,v0.1.0a2]`, `test_version_boundary_forms_rejected[V0.1.0a3,0.1.0a3 ,0.1.0a30,vv0.1.0a3]`, `test_non_string_contract_version_rejected[number,bool,object,array]`, `test_absent_or_empty_contract_version_rejected[empty,absent]`; kept `test_contract_version_mismatch_rejected` (`v0.0.0a0`) as the arbitrary-version case
- [x] 2.2 (GREEN) Added `supabase/migrations/20260706170000_cyl_writeback_contract_a3.sql`: full-body `CREATE OR REPLACE` (byte-identical to the archived body except the version block) pinning `0.1.0a3` and comparing `regexp_replace(coalesce(...,''),'^v','') IS DISTINCT FROM regexp_replace(pinned_version,'^v','')`; re-asserts `OWNER TO postgres` + the byte-identical `REVOKE/GRANT` block; no change-E `DROP POLICY` re-run. Prepended a **cutover safety guard** (`DO` block) that `RAISE`s if any `cyl_trait_sources` row carries a `0.1.0a2` `contract_version`, so the hard cutover fails loudly rather than silently orphaning (verified: passes on a clean DB, raises on a seeded a2 row)
- [x] 2.3 Added `supabase/rollbacks/20260706170000_cyl_writeback_contract_a3_rollback.sql`: full-body `CREATE OR REPLACE` restoring the strict `v0.1.0a2` body verbatim, with a warning that rolling back re-introduces the original tag-vs-package mismatch
- [x] 2.4 Applied the a3 migration to a live Postgres and ran the writeback suite → **68 passed** (incl. the new version cases + `test_execute_grants_*` confirming exactly `bloom_writer`/`service_role`/`bloom_admin`)
- [x] 2.5 Added `test_a3_migration_body_is_idempotent` + `test_a3_rollback_restores_strict_a2` (mirroring the existing `_TS`-keyed idempotency/rollback tests) — both pass

## 3. Update dependent tests and references

- [x] 3.1 `tests/integration/test_cyl_read_path.py`: `PINNED_VERSION = "0.1.0a3"` (seeds via the RPC) — suite **34 passed, 1 skipped**
- [x] 3.2 `tests/integration/test_contract_migration_match.py`: updated the two docstring version references to `v0.1.0a3`; contract-side assertions still pass against the re-pinned schema — suite **12 passed, 1 skipped**. All four `0.1.0a2` grep hits in `tests/` addressed (two `PINNED_VERSION`, two docstrings)

## 4. Validate

- [x] 4.1 `openspec validate repin-cyl-contract-a3 --strict` — valid (2 deltas: `cyl-trait-writeback` + `contract-pinning`)
- [x] 4.2 Ran the cyl integration suites against a live Postgres — writeback 68, read-path 34+1s, migration-match 12+1s (all green). CI re-runs the full suite against the clean compose stack (authoritative)
- [x] 4.3 External premise recorded in the proposal: `v0.1.0a3` tag confirmed in `talmolab/sleap-roots-contracts` (schema fetched + diffed); #393 states the emitter pins `sleap-roots-contracts==0.1.0a3`
- [x] 4.4 Cutover-safety now **enforced at migrate time** by the §2.2 guard (fails loudly if any `a2` row exists), on every environment. The equivalent `SELECT count(*) … LIKE '%0.1.0a2%'` sanity is `0` on the local dev DB; staging/prod are checked automatically by the guard when the migration applies
- [x] 4.5 Migration lint passes (`scripts/lint_migrations.sh origin/staging`). Note: black/ruff pre-commit hooks are scoped to `langchain|bloommcp|services/workflows` and do **not** apply to `tests/integration/` (hand-formatted); no prettier run inside `contracts/`
- [ ] 4.6 Open the bundled PR (proposal + implementation) targeting `staging`; link #393 and the companion `sleap-roots-contracts` convention issue; needs a non-author reviewer (branch protection)
