## 1. Oracle — TS drift guard (write first, watch it fail / RED)

- [ ] 1.1 Add `json-schema-to-typescript` pinned **exact `15.0.4`** to root `package.json`
  `devDependencies`; run `npm install` to update `package-lock.json` (committed).
- [ ] 1.2 Add npm scripts to root `package.json`: `"contracts:gen": "node scripts/contract_types.mjs --write"`
  and `"contracts:check": "node scripts/contract_types.mjs --check"`.
- [ ] 1.3 Write `scripts/contract_types.mjs` (Node ESM): reads `contracts/pin.json` +
  `contracts/schema/result_envelope.schema.json`; **pin-consistency** (assert `pin.json.version`
  == version segment parsed from schema `$id`); generates TS via the json2ts **API** (fixed
  `bannerComment` + options); `--write` writes `contracts/generated/result-envelope.ts`,
  `--check` byte-compares and exits non-zero + prints a diff on mismatch or on missing files.
- [ ] 1.4 Run `node scripts/contract_types.mjs --check` → **RED** (no `contracts/` files yet);
  capture the failure.

## 2. Migration-match test (write first, watch it fail / RED)

- [ ] 2.1 Write `tests/integration/test_contract_migration_match.py`: a module-level declarative
  `MAPPINGS` list of `{contract_field, db_table, db_column, db_type, status, reason}`; loads
  `contracts/schema/result_envelope.schema.json`; uses the `pg_conn` fixture (mirror the
  `_column_type`/`pg_constraint` helpers from `test_cyl_trait_source_provenance.py`).
- [ ] 2.2 Active assertions (A-subset): `cyl_trait_sources.metadata` is `jsonb`;
  `cyl_trait_sources.idempotency_key` is `text` with the non-empty CHECK
  (`cyl_trait_sources_idempotency_key_nonempty`). Tie types back to the loaded contract
  (`Provenance.idempotency_key` is `string`).
- [ ] 2.3 Deferred rows (`source_id` FK / blob table / RPC key-equality) marked
  `status="deferred"` and **skipped with reason** (`pytest.skip`), never asserted.
- [ ] 2.4 Run `uv run --extra test pytest tests/integration/test_contract_migration_match.py`
  against local Supabase → **RED** (vendored contract schema not present yet → load fails);
  capture the failure.

## 3. Vendor the pinned schema + manifest (GREEN: pin-consistency + test can load)

- [ ] 3.1 Copy `C:\repos\sleap-roots-contracts\schema\result_envelope.schema.json` →
  `contracts/schema/result_envelope.schema.json` (byte copy; `$id …/v0.1.0a1/…`).
- [ ] 3.2 Write `contracts/pin.json`: `{ package: "sleap-roots-contracts", version: "v0.1.0a1",
  source: "<github tag/release url>", schema: "schema/result_envelope.schema.json", generated:
  "generated/result-envelope.ts" }`.
- [ ] 3.3 Write `contracts/README.md`: the pinned version, the re-pin procedure
  (`--write` + commit), and the `$id`-restamp-is-a-structural-no-op rule.
- [ ] 3.4 Re-run the migration-match test → **GREEN** (loads the contract; A's columns already
  applied locally).

## 4. Generate + commit the contract TS types (GREEN: drift guard)

- [ ] 4.1 Run `npm run contracts:gen` → writes `contracts/generated/result-envelope.ts`
  (`ResultEnvelope`/`Provenance`/`TraitValue`/`BlobRef` + sub-defs, `DO NOT EDIT` banner).
- [ ] 4.2 Run `npm run contracts:check` → **GREEN** (byte-identical; pin-consistency passes).
- [ ] 4.3 Verify the `$id`-no-op property: temporarily edit the vendored schema's `$id` to a
  different version, re-run `contracts:gen` to a temp path (or re-run `--check`), confirm the
  generated TS is **unchanged**; revert the edit. (Demonstrates the structural no-op; no commit.)

## 5. CI wiring

- [ ] 5.1 Add a step to the `build-and-audit` job in `.github/workflows/pr-checks.yml`:
  `node scripts/contract_types.mjs --check` (after `npm ci`). Name it clearly (contract types
  drift guard).
- [ ] 5.2 Confirm the migration-match test needs **no** workflow change (it auto-runs in
  `compose-health-check`, which globs `tests/integration/`).

## 6. Verify + pre-merge

- [ ] 6.1 `openspec validate pin-sleap-roots-contract --strict` passes.
- [ ] 6.2 Format/lint the new files (`scripts/contract_types.mjs`, `contracts/*`, the pytest
  test) per repo conventions (prettier for JS/JSON/MD; ruff/black for Python); `npm run
  format:check`.
- [ ] 6.3 Run the migration-match test + the existing change-A provenance test locally against
  local Supabase to confirm both pass; run `npm run contracts:check`.
- [ ] 6.4 Run the relevant pre-merge subset (run-ci-locally / pre-merge skill) and confirm the new
  drift-guard step passes.
- [ ] 6.5 Update `tasks.md` checkboxes to reflect reality.
