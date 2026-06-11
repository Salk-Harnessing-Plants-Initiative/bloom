> **Commit/PR safety:** the RED captures in §1–§2 are **local-only**. Do not push a PR until the
> §3/§4 artifacts exist in the same push, so the first pushed (and CI-run) commit is already GREEN.
> CI runs on the PR head, not per-commit.

## 1. Oracle — TS drift guard + determinism config (write first, RED locally)

- [ ] 1.1 Add `json-schema-to-typescript` pinned **exact `15.0.4`** to root `package.json`
  `devDependencies`; run `npm install` to update `package-lock.json` (committed).
- [ ] 1.2 Add npm scripts to root `package.json`: `"contracts:gen": "node scripts/contract_types.mjs --write"`
  and `"contracts:check": "node scripts/contract_types.mjs --check"`.
- [ ] 1.3 Add **determinism config** (prevents the guard from fighting repo tooling):
  - New `.prettierignore` excluding `contracts/generated/` and `contracts/schema/`.
  - Add `contracts/generated/*.ts text eol=lf` to `.gitattributes` (vendored schema already
    covered by the existing `*.json text eol=lf`).
- [ ] 1.4 Write `scripts/contract_types.mjs` (Node ESM): reads `contracts/pin.json` +
  `contracts/schema/result_envelope.schema.json`; **pin-consistency** — assert `pin.json.id` ==
  schema `$id` (exact) AND `pin.json.version` == version parsed from `$id` via anchored regex
  `/\/schema\/(v[^/]+)\/result_envelope\.schema\.json$/`; fail on missing/unparseable `$id`.
  Generates TS via the json2ts **API** (fixed `bannerComment` + options); `--write` writes
  `contracts/generated/result-envelope.ts` (always `\n`); `--check` regenerates in-memory,
  EOL-normalizes both sides (`\r\n`→`\n`), byte-compares, exits non-zero + prints a diff on
  mismatch or on missing files.
- [ ] 1.5 Run `node scripts/contract_types.mjs --check` → **RED** (expected here as a *file-not-found*
  error: no `contracts/` files yet — distinct from the byte-mismatch RED proven in §4); capture it.

## 2. Migration-match characterization test (regression-lock; write first, RED locally)

- [ ] 2.1 Write `tests/integration/test_contract_migration_match.py`: a module-level declarative
  `MAPPINGS` list of `{contract_field, db_table, db_column, db_type, constraint, status, reason}`;
  **load the contract schema lazily** (inside the test/fixture) or guard with
  `pytest.skip(allow_module_level=True)` when `contracts/schema/result_envelope.schema.json` is
  absent — must never crash collection. Reuse the `_column_type`/`pg_constraint` helpers from
  `test_cyl_trait_source_provenance.py`; use the `pg_conn` fixture (rollback per test).
- [ ] 2.2 Active assertions (A-subset), each tied back to the loaded contract:
  - `cyl_trait_sources.metadata` is `jsonb` **and** `schema["$defs"]["Provenance"]["description"]`
    contains the literal `cyl_trait_sources.metadata` (contract-side home tripwire).
  - `cyl_trait_sources.idempotency_key` is `text`, contract `Provenance.idempotency_key` is
    `string`, **and both** constraints exist: `cyl_trait_sources_idempotency_key_nonempty` (CHECK)
    **and** `cyl_trait_sources_idempotency_key_key` (UNIQUE).
- [ ] 2.3 Deferred rows (`source_id` FK / blob table / RPC key-equality / `contract_version`
  validation / `scan_key` resolution) marked `status="deferred"` and **skipped with reason**
  (`pytest.skip`), never asserted. Add a documented (non-asserted) note that remaining `Provenance`
  fields live inside opaque `metadata`, and that `contract_version`/`TraitValue.value` finiteness
  are D/G consumer-side hand-offs.
- [ ] 2.4 Run `uv run --extra test pytest tests/integration/test_contract_migration_match.py`
  against local Supabase → **RED** (module-level skip / load fails before vendoring); capture it.

## 3. Vendor the pinned schema + manifest (GREEN: pin-consistency + test loads)

- [ ] 3.1 Copy `C:\repos\sleap-roots-contracts\schema\result_envelope.schema.json` →
  `contracts/schema/result_envelope.schema.json` (LF-normalized; `$id …/v0.1.0a1/…`).
- [ ] 3.2 Write `contracts/pin.json`: `{ package, version: "v0.1.0a1", id: "<full $id>", source:
  "<github tag/release url>", schema: "schema/result_envelope.schema.json", generated:
  "generated/result-envelope.ts" }`.
- [ ] 3.3 Write `contracts/README.md`: the pinned version (+ the #294 a0/v0.1.0 reconciliation),
  the re-pin procedure (`--write` + commit), and the `$id`-restamp-is-a-structural-no-op rule.
- [ ] 3.4 Re-run the migration-match test → **GREEN** (loads the contract; A's columns already
  applied locally). Then prove the oracle discriminates: temporarily mutate one expected value
  (e.g. assert `metadata` is `"json"`) or drop a constraint inside the `pg_conn` transaction,
  confirm the failure fires **on the assertion line**, then revert.

## 4. Generate + commit the contract TS types (GREEN: drift guard)

- [ ] 4.1 Run `npm run contracts:gen` → writes `contracts/generated/result-envelope.ts`
  (`ResultEnvelope`/`Provenance`/`TraitValue`/`BlobRef` + sub-defs, `DO NOT EDIT` banner).
  **Review** the `BlobRef`/`ResultEnvelope` output is a sane, non-degenerate shape before
  committing (the `anyOf`-over-`properties` `BlobRef` is the risk case).
- [ ] 4.2 Run `npx tsc --noEmit` on the generated file (a throwaway tsconfig or `--strict` flags)
  to confirm it is valid, usable TypeScript.
- [ ] 4.3 Run `npm run contracts:check` → **GREEN** (byte-identical after EOL-normalize;
  pin-consistency passes).
- [ ] 4.4 Write `scripts/contract_types.test.mjs` (Vitest) covering the spec's negative/no-op
  scenarios, all using the **same json2ts invocation** the guard uses:
  - pin-mismatch (in-memory schema/pin with disagreeing version/`$id`) → check returns non-zero.
  - hand-edited committed TS (temp copy) → `--check` returns non-zero with a diff.
  - `$id`-only mutation → regenerated TS **identical** (structural no-op).
  - real field mutation (flip `idempotency_key` type / add a property) → regenerated TS **differs**
    (proves a real change fails).
  - Run `npx vitest run scripts/contract_types.test.mjs` → GREEN.

## 5. CI wiring

- [ ] 5.1 Add two steps to the `build-and-audit` job in `.github/workflows/pr-checks.yml` (after
  `npm ci`): `node scripts/contract_types.mjs --check` (drift guard) and
  `npx vitest run scripts/contract_types.test.mjs` (negative-path/no-op test). Name them clearly.
- [ ] 5.2 Confirm the migration-match test needs **no** workflow change (it auto-runs in
  `compose-health-check`, which globs `tests/integration/`).

## 6. Verify + pre-merge

- [ ] 6.1 `openspec validate pin-sleap-roots-contract --strict` passes.
- [ ] 6.2 Format/lint the new files **excluding the prettier-ignored `contracts/generated/` and
  `contracts/schema/`** (ruff/black for the pytest test; prettier for `scripts/*.mjs`,
  `pin.json`, READMEs); run `npm run format:check` and confirm the generated/schema files are not
  flagged (i.e. `.prettierignore` is effective).
- [ ] 6.3 Run the migration-match test + the existing change-A provenance test locally against
  local Supabase to confirm both pass; run `npm run contracts:check` and the Vitest test on the
  author's (Windows) machine to confirm no CRLF/LF flake.
- [ ] 6.4 Run the relevant pre-merge subset (run-ci-locally / pre-merge skill) and confirm the new
  drift-guard + vitest steps pass and `npm run format` does **not** rewrite the generated file.
- [ ] 6.5 Update `tasks.md` checkboxes to reflect reality.
