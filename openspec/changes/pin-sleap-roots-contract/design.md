## Context

The contract is owned by sub-project #1 (`sleap-roots-contracts`): Pydantic models →
`schema/result_envelope.schema.json`, version-stamped in each schema's `$id`, published to PyPI
and as a GitHub release artifact. Bloom (#2) is the **consumer**: it pins a schema version,
codegens TS, and checks its DB migration against the schema in CI (contract design
`docs/01-contract-library-design.md` §6 "Cross-language flow & drift guard").

Change A (#290, on `staging` at `9b17d31`) already added `cyl_trait_sources.metadata jsonb`
(opaque `Provenance` home) + `idempotency_key text` (UNIQUE + non-empty CHECK), hand-reading the
contract. Its archived design explicitly defers the "CI migration/types-match check" to a later
change — the roadmap renamed it **consume-pin** and sequenced it *first* in A2. This change is
that check.

Two facts shape the design:

1. **Schema `$id` carries the package version.** `result_envelope` is byte-identical between
   `v0.1.0a0` and `v0.1.0a1` *except* its `$id` (`…/v0.1.0a0/…` → `…/v0.1.0a1/…`). The roadmap's
   hard constraint: on any re-pin, **regenerate and accept the `$id`-only diff as a structural
   no-op** — do not treat it as a contract revision.
2. **Bloom's CI has two relevant homes.** `build-and-audit` (Node; runs `npm ci`; no DB) and
   `compose-health-check` (brings up the prod compose stack, applies migrations, runs
   `pytest tests/integration/` against the live DB via the `pg_conn` fixture). The TS drift guard
   belongs in the former; the DB migration-match belongs in the latter.

## Goals / Non-Goals

- **Goals:** pin `result_envelope.schema.json` at an explicit version; codegen committed TS
  types with a CI guard that fails on any drift between committed types and `json-schema → TS` of
  the pinned schema; add a migration-matches-schema CI assertion scoped to what change A built;
  make the `$id`-restamp-no-op rule mechanically enforceable.
- **Non-Goals:** no DB migration (A shipped the columns); no consumer wiring of the generated
  types (deferred to B/C/D/G); no jsonb shape validation at the DB layer (the contract owns
  that, producer-side); no change to the contract repo.

## Decisions

### D1 — Pin `v0.1.0a1` (current), vendored as a committed copy + manifest

Pin the current release so the next *real* re-pin event (to `v0.1.0`) is the only future churn.
`result_envelope` is unchanged from a0, so this is purely about recording the pin.

Vendor a **committed copy** (`contracts/schema/result_envelope.schema.json`) rather than
fetching at CI time: deterministic, offline, reviewable as a diff, and the committed `$id` *is*
the pin record. A separate `contracts/pin.json` records the version explicitly so humans and the
guard have a single declared source of truth. **Pin-consistency check:** `pin.json.version` MUST
equal the version segment parsed from the schema `$id`; they cannot silently disagree. Rejected
fetch-at-CI: adds a network dependency and a "fetched from where" question (the artifact lives on
a git tag / release asset, not a CDN), and makes local TDD reproduction harder.

### D2 — Codegen with `json-schema-to-typescript`, pinned exact `15.0.4`, into `contracts/generated/`

`json-schema-to-typescript` (`json2ts`) is the de-facto JSON-Schema→TS tool and handles draft
2020-12 `$defs`, `anyOf` nullable unions, and enums. **Determinism is the load-bearing property
of a drift guard**, so:

- Pin json2ts to **exact `15.0.4`** in `package.json` (not a caret). The rest of the dependency
  tree (including the prettier json2ts uses to format) is pinned by `package-lock.json`, so
  `npm ci` reproduces byte-identical output in CI (node 20) and locally (node 22).
- A single module `scripts/contract_types.mjs` drives both generation and checking via the
  json2ts API (not the CLI), so generate-and-commit and CI-check run the *identical* invocation
  (same `bannerComment`, same options) — the only way the byte-compare is sound.

Generated types live in a **consumer-neutral** `contracts/generated/result-envelope.ts` with a
`DO NOT EDIT` banner. No code imports them yet (B/C/D/G pending); co-locating them in a package
(e.g. `bloom-js`) now would couple the artifact to that package's build/lint before any consumer
exists. The eventual consumer can import from `contracts/generated/` or the file can move with a
deliberate refactor when D/G need it.

**`$id`-no-op enforceability (the key mechanism):** json2ts never emits `$id` into the generated
types. So a future re-pin that only re-stamps `$id` regenerates **byte-identical** TS → the drift
guard passes with a *zero* TS diff, mechanically proving the structural no-op. The intended,
reviewable diff on a re-pin is exactly two things kept in lockstep by the pin-consistency check:
the schema `$id` and `pin.json.version`.

### D3 — Migration-match: pytest DB-introspection, A-subset, declaratively extensible

A pytest integration test (`tests/integration/test_contract_migration_match.py`) introspects the
**actually-applied** DB (via `pg_conn`) and compares against the vendored contract schema. This
asserts the real schema, not parsed SQL text, and reuses the established change-A test pattern; it
auto-runs in `compose-health-check` (the job globs `tests/integration/`), so no workflow edit is
needed for it.

A **declarative mapping** (a list of `{contract_field, db_table, db_column, db_type, status}`
entries) drives the assertions:

- **Active (built by change A):**
  - `Provenance` envelope → `cyl_trait_sources.metadata` is `jsonb` (the contract's `Provenance`
    docstring states it *"serializes to cyl_trait_sources.metadata jsonb"*).
  - `Provenance.idempotency_key` (contract type `string`, `default: ""`) →
    `cyl_trait_sources.idempotency_key` is `text` **and** the non-empty CHECK exists (the DB
    posture matching the contract's `""` default — an empty key is not valid).
- **Deferred (skipped with a reason, not asserted):** B (`source_id` FK on the trait tables),
  C (intermediates/blob table), D (`idempotency_key == metadata->>'idempotency_key'` equality,
  RPC-enforced). Marked `status="deferred"` so the file is a living extension point: each future
  change flips its row to active. This keeps the check meaningful today and **not brittle**
  against tables/columns that do not exist yet.

The test also records (as a documented, non-asserted note) that the remaining `Provenance` fields
are deliberately carried *inside* the opaque `metadata` jsonb, not promoted to columns — change
A's intentional boundary.

### D4 — CI wiring

- TS drift guard: a new step in `build-and-audit` → `node scripts/contract_types.mjs --check`
  (runs after `npm ci`, before/after the existing build steps). Pure Node, fast, no DB.
- Migration-match: no workflow change — placing the test under `tests/integration/` is sufficient
  (`compose-health-check` runs the whole directory).

## Risks / Trade-offs

- **json2ts CVE surface.** Adding a devDependency widens `npm audit --audit-level=critical`
  (already in `build-and-audit`). json2ts is widely used; if a critical advisory appears it is
  handled like any other dep. → Accept; exact-pin keeps the surface explicit and reviewable.
- **json2ts output stability across versions.** A json2ts upgrade could change formatting and
  trip the guard. → That is *correct* behavior (the guard owns the committed bytes): an upgrade is
  a deliberate `--write` + commit, gated by the exact pin. The risk is only an *unexpected* bump,
  which the exact pin + lockfile prevent.
- **Unusual `BlobRef` shape.** `BlobRef` has a top-level `anyOf` (s3 *or* box required) layered
  over a `properties` block; json2ts emits a union/intersection. Whatever it emits is committed and
  guarded — correctness of the *type* matters less than determinism for the guard, and the
  producer-side contract owns semantic validity.
- **Migration-match needs the compose stack.** It only runs in the heavier
  `compose-health-check` job. → Acceptable: it reuses existing infra and asserts the real applied
  schema, which is the meaningful target; the cheap TS guard runs in the fast Node job.

## Migration Plan

No database migration. Pure additive repo content (vendored schema, manifest, generated types,
guard script, CI step, test) + one devDependency. Rollback = revert the change; nothing is
applied to any database. Re-pin procedure (documented in `contracts/README.md`): replace the
vendored schema, bump `pin.json.version`, run `npm run contracts:gen`, commit; the `$id`-only diff
regenerates identical types and the guards pass.

## Open Questions

- None blocking. The cleaner long-term home for the migration-match requirement is the
  `cyl-trait-writeback` capability (where B/C/D already edit), but that capability is not a live
  spec on `staging` until change A's archive (#300) lands. Keeping all three requirements in the
  new `contract-pinning` capability now avoids coupling this change to #300; relocating req 3 is a
  deliberate later refactor.
