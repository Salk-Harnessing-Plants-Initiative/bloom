# Pin sleap-roots contract (A2 consume-pin, #294)

## Why

The sleap-roots ↔ Bloom integration's most failure-prone seam is the **cross-language
contract boundary**: the `sleap-roots-contracts` `ResultEnvelope`/`Provenance` shape is
authored in Python (Pydantic → JSON Schema), and Bloom (TypeScript + Postgres) consumes it.
Today Bloom consumes it only *implicitly* — change A (#290) added `cyl_trait_sources.metadata`
(the opaque `Provenance` envelope home) and `idempotency_key` by hand-reading the contract,
with **no machine check** that Bloom's generated types or DB schema still agree with a specific
contract version. The contract can change, or Bloom can drift, and nothing fails.

This is the **A2 "consume (pin)" change (#294)**. The roadmap sequences it *first* in A2 and
notes change A's "types-match-contract CI" was always meant to depend on it (A shipped first
pragmatically; this closes that gap). It pins the contract at an explicit version, codegens the
TypeScript types from its JSON Schema, and adds a **migration-matches-schema CI check** so
Bloom's generated types and DB schema cannot silently drift from the pinned contract.

Per the roadmap's **contract version pinning** hard constraint, the schema `$id` is
version-stamped, so any future re-pin re-stamps `result_envelope`'s `$id` with **no payload
change**. The design makes that an enforceable structural no-op (codegen ignores `$id`, so a
`$id`-only re-pin regenerates byte-identical types), and an automated test proves the converse:
a *real* field change does fail the guard.

## What Changes

- **Vendor the pinned contract** (`v0.1.0a1`, current) as a committed, LF-normalized copy under
  `contracts/`:
  - `contracts/schema/result_envelope.schema.json` — the published a1 schema
    (`$id …/v0.1.0a1/…`), normalized to LF and excluded from repo prettier (see Determinism
    below) so it stays a faithful copy of the published artifact.
  - `contracts/pin.json` — a manifest recording the pinned `package`, `version`, the **full
    `$id` string**, `source`, and the schema/generated file paths. The **pin is the committed
    `$id`**; the manifest records it explicitly.
  - `contracts/README.md` — documents the pin, the `$id`-restamp-is-a-no-op rule, and the
    re-pin procedure.
  - **Version choice vs #294:** #294 names `v0.1.0a0` and states a preference for a
    non-prerelease `v0.1.0` cut. That release does not exist yet, so we pin the **current
    `v0.1.0a1`** (`result_envelope` is byte-identical to a0 except its `$id`); the `$id`-no-op
    machinery makes the eventual `v0.1.0` re-pin a zero-TS-diff event.
- **Codegen TypeScript types** from the pinned schema with `json-schema-to-typescript`
  (added as a root devDependency, **pinned to exact `15.0.4`** for deterministic output),
  generated into `contracts/generated/result-envelope.ts` (`ResultEnvelope`, `Provenance`,
  `TraitValue`, `BlobRef`, and their sub-defs). This is the *contract* types — distinct from the
  Supabase `database.types.ts` (generated from the DB by `make gen-types`).
- **TS drift guard (the oracle):** a Node ESM script `scripts/contract_types.mjs` with `--write`
  (regenerate) and `--check` (regenerate in-memory, EOL-normalize, byte-compare to the committed
  file, exit 1 + diff on mismatch) modes, plus a **pin-consistency check** that `pin.json.version`
  and `pin.json.id` agree with the schema's `$id` (exact-string match on the full `$id`; the
  version segment is parsed with an anchored regex). The script **exports pure functions** (`async
  generateTypes`, `checkPinConsistency`, `async checkDrift`; json2ts `compile()` is async — no file
  I/O or `process.exit` inside them) behind a thin CLI shim, so it is unit-testable. Wired into the
  existing `build-and-audit` CI job (Node, no DB). A companion `node --test` file
  (`scripts/contract_types.test.mjs`, also in `build-and-audit`; uses the built-in Node test
  runner, no new framework) exercises the guard's negative paths and the `$id`-no-op property in
  both directions (see Testing in design.md / tasks.md).
- **Determinism guards (new repo config):** a `.prettierignore` excluding `contracts/generated/`
  and `contracts/schema/` (json2ts owns the generated file's format; the vendored schema is a
  faithful copy), and a `.gitattributes` rule `contracts/generated/*.ts text eol=lf` so the guard
  is reproducible on Windows (local) and Linux (CI). Without these, repo prettier would reflow the
  generated file and CRLF/LF would diverge — both breaking the byte-equal guard.
- **Migration-matches-schema CI check:** a pytest integration test
  (`tests/integration/test_contract_migration_match.py`) that introspects the live applied DB
  (via the existing `pg_conn` fixture, in `compose-health-check`) and asserts the contract↔DB
  mappings **built today**:
  - the `Provenance` envelope home is `cyl_trait_sources.metadata` jsonb, **and** the contract
    still designates that home (the loaded schema's `Provenance.description` names
    `cyl_trait_sources.metadata` — a tripwire on a contract-side re-home);
  - `Provenance.idempotency_key` (contract type `string`, `default: ""`) maps to
    `cyl_trait_sources.idempotency_key` text with **both** the non-empty CHECK
    (`cyl_trait_sources_idempotency_key_nonempty`, asserted by `contype = 'c'`) **and** the UNIQUE
    constraint (`cyl_trait_sources_idempotency_key_key`, asserted by `contype = 'u'` — a name match
    alone doesn't prove it is a UNIQUE rather than some other constraint) — the UNIQUE is the
    actual 1-envelope:1-row anchor, not just the empty-string guard;
  - **contract-side sanity** (schema facts that justify change A's DB choices, asserted in this one
    home): `Provenance.contract_version` is `required` + `string` (the per-row provenance anchor),
    and `Provenance.idempotency_key.default` is `""` (the documented basis for the non-empty CHECK).
  A declarative mapping marks the B/C/D mappings (`source_id` FK, blob table, RPC key-equality,
  `scan_key`→`cyl_scans` resolution) **deferred/skipped**, so the check is meaningful now and
  extends as those changes land — never asserting against tables that do not exist yet. The schema
  is **loaded lazily / module-level-skipped** when absent so it can never crash pytest collection.

Non-goals (separate changes): no DB migration here (change A already shipped the columns); no
consumer is wired to the generated types yet (changes B/C/D/G pending) — the artifact + guards
exist now, consumption lands with the consumer; no change to the contract itself (owned by
sub-project #1, `sleap-roots-contracts`). **`schema/analysis_input.schema.json`** (the
downstream sleap-roots-analyze *input* contract, not the write-back result envelope) is
intentionally out of scope — only `result_envelope` is consumed by Bloom's A2 write-back, so
`schema/*.json` in #294 is narrowed to the one schema Bloom consumes.

## Impact

- Affected specs: `contract-pinning` (new capability). NB: the migration-matches-schema
  requirement is housed here as an interim home; its natural long-term home is
  `cyl-trait-writeback` (where B/C/D edit), and relocating it is a deliberate refactor deferred
  until change A's archive (#300) lands that live spec on `staging`.
- Affected code:
  - `contracts/` (new: vendored schema, pin manifest, generated types, README)
  - `scripts/contract_types.mjs` (new drift guard, pure-function exports + CLI shim) +
    `scripts/contract_types.test.mjs` (new `node --test`) + root `package.json`/
    `package-lock.json` (json2ts devDep + `contracts:gen`/`contracts:check` scripts)
  - `.prettierignore` (new) + `.gitattributes` (one new rule)
  - `.github/workflows/pr-checks.yml` (new drift-guard + `node --test` steps in `build-and-audit`)
  - `tests/integration/test_contract_migration_match.py` (new; auto-runs in
    `compose-health-check`)
- Affected issues: A2 consume-pin **#294** (parent #12, EPIC #9); unblocks/back-fills change A's
  intended types-match-contract CI; precedes change B (#295)
- Data: none — no migration, no schema change, no row changes
- Cross-change hand-offs (recorded so later changes don't drop them):
  - The migration-match check is the extension point for B (#295, `source_id` FK), C (#296, blob
    table), and D (write-back RPC; tracked under #13, sub-issue TBD; co-lands with E #297 — note
    #297 is change **E**, the RLS lockdown, not D). Each fills in its deferred mapping row when it
    lands.
  - **`Provenance.contract_version`** is the per-row traceability anchor (which contract an
    envelope was produced under). This change pins the *consumer* version but cannot assert
    runtime rows; change **D/G's consumer MUST validate `provenance.contract_version` against
    `pin.json.version`** (or an explicit compatibility set) at the consume boundary. Recorded as a
    deferred mapping row + design hand-off so it is not forgotten.
  - **`TraitValue.value`** must be finite-or-null (the contract normalizes NaN/inf → null); change
    **G's consumer MUST validate** finiteness at the write boundary. Recorded as a hand-off.
