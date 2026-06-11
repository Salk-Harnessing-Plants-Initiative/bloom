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
`$id`-only re-pin regenerates byte-identical types).

## What Changes

- **Vendor the pinned contract** (`v0.1.0a1`, current) as a committed copy under `contracts/`:
  - `contracts/schema/result_envelope.schema.json` — byte copy of the published a1 schema
    (`$id …/v0.1.0a1/…`).
  - `contracts/pin.json` — a manifest recording the pinned `package` + `version` + `source` +
    `schema`/`generated` paths. The **pin is the committed `$id`**; the manifest records it
    explicitly.
  - `contracts/README.md` — documents the pin, the `$id`-restamp-is-a-no-op rule, and the
    re-pin procedure.
- **Codegen TypeScript types** from the pinned schema with `json-schema-to-typescript`
  (added as a root devDependency, **pinned to exact `15.0.4`** for deterministic output),
  generated into `contracts/generated/result-envelope.ts` (`ResultEnvelope`, `Provenance`,
  `TraitValue`, `BlobRef`, and their sub-defs). This is the *contract* types — distinct from the
  Supabase `database.types.ts` (generated from the DB by `make gen-types`).
- **TS drift guard (the oracle):** a Node ESM script `scripts/contract_types.mjs` with `--write`
  (regenerate) and `--check` (regenerate in-memory, byte-compare to the committed file, exit 1 +
  diff on mismatch) modes, plus a **pin-consistency check** that `pin.json.version` equals the
  version segment parsed from the schema `$id`. Wired into the existing `build-and-audit` CI job
  (Node, no DB).
- **Migration-matches-schema CI check:** a pytest integration test
  (`tests/integration/test_contract_migration_match.py`) that introspects the live applied DB
  (via the existing `pg_conn` fixture, in `compose-health-check`) and asserts the contract↔DB
  mappings **built today**: the `Provenance` envelope home is `cyl_trait_sources.metadata` jsonb,
  and `Provenance.idempotency_key` (contract type `string`, `default: ""`) maps to
  `cyl_trait_sources.idempotency_key` text with the non-empty CHECK. A declarative mapping marks
  the B/C/D mappings (`source_id` FK, blob table, RPC equality) **deferred/skipped**, so the
  check is meaningful now and extends as those changes land — never asserting against tables that
  do not exist yet.

Non-goals (separate changes): no DB migration here (change A already shipped the columns); no
consumer is wired to the generated types yet (changes B/C/D/G pending) — the artifact + guards
exist now, consumption lands with the consumer; no change to the contract itself (owned by
sub-project #1, `sleap-roots-contracts`).

## Impact

- Affected specs: `contract-pinning` (new capability)
- Affected code:
  - `contracts/` (new: vendored schema, pin manifest, generated types, README)
  - `scripts/contract_types.mjs` (new drift guard) + root `package.json`/`package-lock.json`
    (json2ts devDep + `contracts:gen`/`contracts:check` scripts)
  - `.github/workflows/pr-checks.yml` (new drift-guard step in `build-and-audit`)
  - `tests/integration/test_contract_migration_match.py` (new; auto-runs in
    `compose-health-check`)
- Affected issues: A2 consume-pin **#294**; unblocks/back-fills change A's intended
  types-match-contract CI; precedes change B (#295)
- Data: none — no migration, no schema change, no row changes
- Cross-change: the migration-match check is written as the extension point for B (#295,
  `source_id` FK), C (#296, blob table), and D (#297/#13, write-back RPC); each fills in its
  deferred mapping row when it lands. Relocating the migration-match requirement into the
  `cyl-trait-writeback` capability is a deliberate future refactor (deferred until change A's
  archive lands that live spec on `staging`).
