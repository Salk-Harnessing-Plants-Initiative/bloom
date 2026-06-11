> **Commit/PR safety:** the RED captures in §1–§2 are **local-only**. Do not push a PR until the
> §3/§4 artifacts exist in the same push, so the first pushed (and CI-run) commit is already GREEN.
> CI runs on the PR head, not per-commit. (This safety depends on §2.1's collection guard: a
> pushed pre-vendor state must skip, not crash the integration suite.)

## 1. Oracle — TS drift guard + determinism config (write first, RED locally)

- [ ] 1.1 Add `json-schema-to-typescript` pinned **exact `15.0.4`** to root `package.json`
  `devDependencies`; run `npm install` to update `package-lock.json` (committed).
- [ ] 1.2 Add npm scripts to root `package.json`: `"contracts:gen": "node scripts/contract_types.mjs --write"`
  and `"contracts:check": "node scripts/contract_types.mjs --check"`.
- [ ] 1.3 Add **determinism config** (prevents the guard from fighting repo tooling):
  - New `.prettierignore` excluding `contracts/generated/` and `contracts/schema/`.
  - Add `contracts/generated/*.ts text eol=lf` to `.gitattributes` (vendored schema already
    covered by the existing `*.json text eol=lf`).
- [ ] 1.4 Write `scripts/contract_types.mjs` (Node ESM) as **pure functions + a thin CLI shim**:
  - Exports (no file I/O, no `process.exit` inside them): `generateTypes(schema) → string`
    (json2ts **API**, fixed `bannerComment` + options; always `\n` line endings);
    `checkPinConsistency(pin, schema) → {ok, error}` — `pin.id` exactly equals schema `$id`, and
    `pin.version` equals the version parsed from `$id` via anchored regex
    `/\/schema\/(v[^/]+)\/result_envelope\.schema\.json$/`; fail on missing/unparseable `$id`;
    `checkContractSanity(schema) → {ok, error}` — `contract_version` is in
    `$defs.Provenance.required` and typed `string`; `checkDrift({schema, pin, committedTs}) →
    {ok, diff}` — regenerate, EOL-normalize both sides (`\r\n`→`\n`), byte-compare.
  - CLI shim guarded by `if (import.meta.url === pathToFileURL(process.argv[1]).href)` is the
    **only** place that reads `contracts/pin.json` + `contracts/schema/...` + the committed
    `contracts/generated/...`, calls the pure functions, prints diffs, and `process.exit`s.
    `--write` writes the generated file; `--check` runs all three pure checks and exits non-zero
    on any failure or missing file.
- [ ] 1.5 Run `node scripts/contract_types.mjs --check` → **RED** (expected here as a *file-not-found*
  error: no `contracts/` files yet — distinct from the byte-mismatch RED proven in §4); capture it.

## 2. Migration-match characterization test (regression-lock; write first, RED locally)

- [ ] 2.1 Write `tests/integration/test_contract_migration_match.py`. Structure for collection
  safety: `MAPPINGS` is a module-level list of **literals only** (no schema-derived values) —
  `{contract_field, db_table, db_column, db_type, constraint, contype, status, reason}`. A
  `_load_schema()` helper reads `contracts/schema/result_envelope.schema.json` **lazily inside the
  test body** and does `pytest.skip("contract schema not vendored", allow_module_level=False)` (or
  a module-level `allow_module_level=True` skip if the file is absent) so collection never crashes.
  Reuse `_column_type`/`pg_constraint` patterns + the `pg_conn` fixture from
  `test_cyl_trait_source_provenance.py` (rollback per test).
- [ ] 2.2 Active assertions (A-subset), each tied back to the lazily-loaded contract:
  - `cyl_trait_sources.metadata` is `jsonb` **and** `schema["$defs"]["Provenance"]["description"]`
    contains the literal `cyl_trait_sources.metadata` (contract-side home tripwire).
  - `cyl_trait_sources.idempotency_key` is `text`; contract `Provenance.idempotency_key` is
    `string`; **both** constraints exist *and have the right `contype`* (query `conname, contype`
    from `pg_constraint`): `cyl_trait_sources_idempotency_key_nonempty` with `contype='c'` (CHECK)
    and `cyl_trait_sources_idempotency_key_key` with `contype='u'` (UNIQUE). Name alone is
    insufficient — assert the type.
- [ ] 2.3 Deferred rows (`source_id` FK / blob table / RPC key-equality / `contract_version`
  runtime validation / `scan_key` resolution) marked `status="deferred"` and **skipped with a
  one-line reason** (`pytest.skip`), never asserted. Keep these mechanical — the narrative
  hand-offs (opaque-metadata fields incl. `inputs.images_checksum`/`param_hash`; `contract_version`
  + `TraitValue.value` finiteness as D/G validations) live in `contracts/README.md` + the proposal,
  **not** as prose in the test.
- [ ] 2.4 Run `uv run --extra test pytest tests/integration/test_contract_migration_match.py`
  against local Supabase → **RED** (module-level skip / load fails before vendoring); capture it.

## 3. Vendor the pinned schema + manifest (GREEN: pin-consistency + test loads)

- [ ] 3.1 Copy `C:\repos\sleap-roots-contracts\schema\result_envelope.schema.json` →
  `contracts/schema/result_envelope.schema.json` (LF-normalized; `$id …/v0.1.0a1/…`).
- [ ] 3.2 Write `contracts/pin.json`: `{ package, version: "v0.1.0a1", id: "<full $id>", source:
  "<github tag/release url>", schema: "schema/result_envelope.schema.json", generated:
  "generated/result-envelope.ts" }`.
- [ ] 3.3 Write `contracts/README.md`: the pinned version (+ the #294 a0/v0.1.0 reconciliation),
  the re-pin procedure (`--write` + commit), the `$id`-restamp-is-a-structural-no-op rule, the
  recorded D/G hand-offs (`contract_version` + `TraitValue.value` finiteness validation), and the
  caveat **"never run prettier from inside `contracts/`"** (the `.prettierignore` is root-relative).
- [ ] 3.4 Re-run the migration-match test → **GREEN** (loads the contract; A's columns already
  applied locally). Then prove the oracle discriminates with the **canonical deterministic step**:
  temporarily change one expected literal (e.g. assert `metadata` is `"json"` instead of `"jsonb"`),
  run, confirm the failure fires **on that assertion** (note the exact assert message/line), then
  revert. (This needs no DB mutation and is repeatable — preferred over dropping a constraint.)

## 4. Generate + commit the contract TS types (GREEN: drift guard)

- [ ] 4.1 Run `npm run contracts:gen` → writes `contracts/generated/result-envelope.ts`
  (all six `$defs`: `ResultEnvelope`/`Provenance`/`TraitValue`/`BlobRef` + `InputRef`/`ModelRef`/
  `ResolvedParams`, `DO NOT EDIT` banner). **Review** all six are sane, non-degenerate shapes
  before committing (the `anyOf`-over-`properties` `BlobRef` is the risk case).
- [ ] 4.2 Type-check the generated file (exact flags, no throwaway tsconfig):
  `npx tsc --noEmit --strict --target ES2020 --moduleResolution bundler --skipLibCheck
  contracts/generated/result-envelope.ts` → no errors (generation-time gate; the drift guard
  freezes the bytes thereafter).
- [ ] 4.3 Run `npm run contracts:check` → **GREEN** (byte-identical after EOL-normalize;
  pin-consistency + contract-sanity pass).
- [ ] 4.4 Write `scripts/contract_types.test.mjs` (**`node --test`**, importing the pure functions
  from `contract_types.mjs` — never spawning the exiting CLI) covering the spec's negative/no-op
  scenarios, all via the **same `generateTypes`** the guard uses:
  - pin-mismatch (in-memory schema/pin with disagreeing `version`/`id`) → `checkPinConsistency`
    returns `{ok:false}`.
  - unparseable/missing `$id` → `checkPinConsistency` returns `{ok:false}`.
  - missing `contract_version` in `Provenance.required` → `checkContractSanity` returns `{ok:false}`.
  - hand-edited committed TS (in-memory string) → `checkDrift` returns `{ok:false}` with a diff.
  - `$id`-only mutation → `generateTypes` output **identical** (structural no-op).
  - real field mutation (flip `idempotency_key` type / add a property) → `generateTypes` output
    **differs** (proves a real change fails).
  - Run `node --test scripts/contract_types.test.mjs` → GREEN.

## 5. CI wiring

- [ ] 5.1 Add two steps to the `build-and-audit` job in `.github/workflows/pr-checks.yml` (after
  `npm ci`), clearly named: `node scripts/contract_types.mjs --check` (drift guard +
  pin-consistency + contract-sanity) and `node --test scripts/contract_types.test.mjs`
  (negative-path/no-op test). No new vitest config — built-in Node runner.
- [ ] 5.2 Confirm the migration-match test needs **no** workflow change (it auto-runs in
  `compose-health-check`, which globs `tests/integration/`).

## 6. Verify + pre-merge

- [ ] 6.1 `openspec validate pin-sleap-roots-contract --strict` passes.
- [ ] 6.2 Format/lint the new files **excluding the prettier-ignored `contracts/generated/` and
  `contracts/schema/`** (ruff/black for the pytest test; prettier for `scripts/*.mjs`,
  `pin.json`, READMEs); run `npm run format:check` and confirm the generated/schema files are not
  flagged (i.e. `.prettierignore` is effective) and that `npm run format` does **not** rewrite the
  generated file. Also confirm the pre-commit prettier hook leaves the generated file unchanged.
- [ ] 6.3 Run the migration-match test + the existing change-A provenance test locally against
  local Supabase to confirm both pass; run `npm run contracts:check` and
  `node --test scripts/contract_types.test.mjs` on the author's (Windows, `core.autocrlf=true`)
  machine to confirm no CRLF/LF flake — this is the worst-case config for the byte guard.
- [ ] 6.4 Run the relevant pre-merge subset (run-ci-locally / pre-merge skill) and confirm the new
  drift-guard + node-test steps pass.
- [ ] 6.5 Update `tasks.md` checkboxes to reflect reality.
