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
   + its Vitest test belong in the former; the DB migration-match belongs in the latter.

## Goals / Non-Goals

- **Goals:** pin `result_envelope.schema.json` at an explicit version; codegen committed TS
  types with a CI guard that fails on any drift between committed types and `json-schema → TS` of
  the pinned schema; add a migration-matches-schema CI assertion scoped to what change A built;
  make the `$id`-restamp-no-op rule mechanically enforceable *and* prove a real change fails.
- **Non-Goals:** no DB migration (A shipped the columns); no consumer wiring of the generated
  types (deferred to B/C/D/G); no jsonb shape validation at the DB layer (the contract owns
  that, producer-side); no change to the contract repo; `analysis_input.schema.json` (downstream
  analysis input) is out of scope — only `result_envelope` is consumed by write-back.

## Decisions

### D1 — Pin `v0.1.0a1` (current), vendored as a committed copy + manifest

Pin the current release so the next *real* re-pin event (to `v0.1.0`) is the only future churn.
`result_envelope` is unchanged from a0, so this is purely about recording the pin. #294 names a0
and prefers a non-prerelease `v0.1.0`; that cut does not exist yet, so a1 is the current pin and
the `$id`-no-op machinery makes the eventual `v0.1.0` re-pin a zero-TS-diff event — the issue's
preference is met *when the release exists*, without blocking now.

Vendor a **committed copy** (`contracts/schema/result_envelope.schema.json`) rather than
fetching at CI time: deterministic, offline, reviewable as a diff, and the committed `$id` *is*
the pin record. It is a **faithful, LF-normalized copy** (not a literal byte copy — `.gitattributes`
forces `*.json eol=lf`, and `.prettierignore` keeps repo prettier from reflowing it). A separate
`contracts/pin.json` records the pin explicitly:

```json
{ "package": "sleap-roots-contracts", "version": "v0.1.0a1",
  "id": "https://github.com/talmolab/sleap-roots-contracts/schema/v0.1.0a1/result_envelope.schema.json",
  "source": "<github tag/release url>",
  "schema": "schema/result_envelope.schema.json", "generated": "generated/result-envelope.ts" }
```

**Pin-consistency check** (in `contract_types.mjs`):

- `pin.json.id` MUST exactly equal the schema's `$id` (exact-string match — cannot mis-parse).
- `pin.json.version` MUST equal the version segment parsed from `$id` with an **anchored regex**
  (`/\/schema\/(v[^/]+)\/result_envelope\.schema\.json$/`), not a positional `split`.
- A missing/unparseable `$id`, or any disagreement, exits non-zero. Rejected fetch-at-CI: adds a
  network dependency and a "fetched from where" question (the artifact lives on a git tag /
  release asset, not a CDN), and makes local TDD reproduction harder.

### D2 — Codegen with `json-schema-to-typescript` (exact `15.0.4`), determinism owned end-to-end

`json-schema-to-typescript` (`json2ts`) handles draft 2020-12 `$defs`, `anyOf` nullable unions,
and enums. **Determinism is the load-bearing property of a drift guard**, and there are three
independent ways it can break — all must be closed, not asserted away:

1. **Tool/version drift.** Pin json2ts to **exact `15.0.4`** (not a caret); the whole tree
   (including the prettier json2ts uses) is frozen by `package-lock.json`, so `npm ci` reproduces
   identical output in CI (node 20) and locally (node 22). json2ts `engines.node` is `>=16`, so
   node 20 is fine. A single module `scripts/contract_types.mjs` **exports pure functions** —
   `generateTypes(schema) → string`, `checkPinConsistency(pin, schema) → {ok, error}`,
   `checkDrift({schema, pin, committedTs}) → {ok, diff}`, and a `checkContractSanity(schema)` that
   asserts the pinned schema still requires the `Provenance.contract_version` anchor — that take
   inputs as **arguments** (no file reads, no `process.exit` inside them). A thin CLI shim
   (`if (import.meta.url === pathToFileURL(process.argv[1]).href)`) is the **only** place that
   reads files and calls `process.exit`. Both `--write` and `--check` go through the same pure
   `generateTypes` (identical `bannerComment` + options), so generate and check run the same
   invocation — the only way the byte-compare is sound — and the test suite drives the pure
   functions directly with in-memory inputs (it never spawns the exiting CLI).
2. **Formatter conflict.** json2ts formats its output with its **own** bundled prettier style
   (`semi:true, singleQuote:false, printWidth:120, trailingComma:'none', bracketSpacing:false`),
   which disagrees with the repo's `.prettierrc.json`
   (`semi:false, singleQuote:true, printWidth:100, trailingComma:'es5'`) on five axes. The repo's
   `format`/`format:check` scripts glob `**/*.{js,jsx,ts,tsx,json,md}` and there is **no
   `.prettierignore`**. So `npm run format` would rewrite `contracts/generated/result-envelope.ts`
   to repo style → the next `contracts:check` regenerates json2ts-style bytes → **guard fails on a
   file nobody touched** (and `format:check`, run by the local pre-merge/lint skills, flags it).
   Fix: a new `.prettierignore` excludes `contracts/generated/` and `contracts/schema/`. json2ts
   owns the generated file's format; the vendored schema is a faithful copy of the published
   artifact, not ours to reformat. (`format:check` is not a CI job today, so this is a *local*
   pre-merge footgun — but tasks §6 runs those skills, so it must be fixed.)
3. **EOL drift (Windows vs Linux).** `.gitattributes` has `* text=auto` and **no `*.ts` rule**, so
   `.ts` round-trips through CRLF in a Windows working tree while json2ts emits `\n`. A naive
   byte-compare then **passes in CI (LF) and fails on the author's Windows machine (CRLF)** — the
   worst kind of flake. Fix (belt and suspenders): add `contracts/generated/*.ts text eol=lf` to
   `.gitattributes` (working tree is LF even on Windows), **and** have `--write` always write `\n`
   and `--check` EOL-normalize (`\r\n`→`\n`) before comparing.

Generated types live in a **consumer-neutral** `contracts/generated/result-envelope.ts` with a
`DO NOT EDIT` banner. No code imports them yet (B/C/D/G pending); co-locating them in a package
(e.g. `bloom-js`) now would couple the artifact to that package's build/lint before any consumer
exists. At generation time (not as a standing CI step — the bytes are frozen by the drift guard,
so type-validity only needs re-checking when they change), a task runs
`npx tsc --noEmit --strict --target ES2020 --moduleResolution bundler --skipLibCheck
contracts/generated/result-envelope.ts` (exact flags, no throwaway tsconfig file) so the committed
types are valid, usable TS; the generated `$defs` (`ResultEnvelope`, `Provenance`, `TraitValue`,
`BlobRef`, plus sub-defs `InputRef`, `ModelRef`, `ResolvedParams`) are eyeballed for non-degeneracy
— the `BlobRef` `anyOf`-over-`properties` shape is the risk case, reviewed not rubber-stamped.

**`$id`-no-op enforceability (the key mechanism) — and its converse.** json2ts never emits `$id`
into the generated types. So a re-pin that only re-stamps `$id` regenerates **byte-identical** TS →
the drift guard passes with a *zero* TS diff, mechanically proving the structural no-op. The
intended, reviewable diff on a re-pin is exactly two things kept in lockstep by the
pin-consistency check: the schema `$id`/`pin.json.id` and `pin.json.version`. A Vitest test
(`scripts/contract_types.test.mjs`) proves **both directions** so the property can't silently
regress: (a) mutate only the `$id` version → regenerated TS is identical; (b) mutate a *real*
field (flip `idempotency_key`'s type / add a property) → regenerated TS **differs** and the guard
would fail. It also covers the negative paths the spec promises: pin-mismatch → not-ok; an
unparseable `$id` → not-ok; a hand-edited committed TS → not-ok. The test is a **`node --test`**
file (`scripts/contract_types.test.mjs`) importing the pure functions — no new test framework,
no config (vitest is already used under `web/`, but the built-in runner avoids the root-config
question entirely). It runs in `build-and-audit` (no DB).

### D3 — Migration-match: pytest DB-introspection, A-subset, declaratively extensible

A pytest **regression-lock / characterization** test
(`tests/integration/test_contract_migration_match.py`) introspects the **actually-applied** DB
(via `pg_conn`) and compares against the vendored contract schema. It asserts the real schema, not
parsed SQL text, reuses the change-A test pattern (`_column_type`, `pg_constraint` queries), and
auto-runs in `compose-health-check` (the job globs `tests/integration/`), so no workflow edit is
needed for it.

It is honestly a **regression-lock over already-landed change A**, not a fresh RED→GREEN cycle:
A's columns already exist, so the assertions are green as soon as the contract schema is vendored.
The genuine "does the oracle discriminate?" proof is a deliberate task that mutates an expected
value (or drops a constraint inside the `pg_conn` rollback transaction), confirms the failure
fires **on the assertion line** (not a file-load error), then reverts.

**Collection safety:** the contract schema is loaded **lazily** (inside the test/fixture) or
guarded with `pytest.skip(allow_module_level=True)` when the file is absent, so a commit where the
test exists but the schema does not can never crash collection for the whole `tests/integration/`
directory.

A **declarative mapping** (a list of `{contract_field, db_table, db_column, db_type, constraint,
status, reason}` entries) drives the assertions:

- **Active (built by change A):**
  - `Provenance` envelope → `cyl_trait_sources.metadata` is `jsonb` — **and** the loaded schema's
    `$defs.Provenance.description` still names `cyl_trait_sources.metadata` as the home (a tripwire:
    if the contract re-homes Provenance via its docstring, json2ts can't see it and the byte-equal
    TS guard would wave it through, so this is the *only* place a description-level re-home is
    caught). Asserted as a substring match on the literal `cyl_trait_sources.metadata`.
  - `Provenance.idempotency_key` (contract type `string`, `default: ""`) →
    `cyl_trait_sources.idempotency_key` is `text` **with both** the non-empty CHECK
    (`cyl_trait_sources_idempotency_key_nonempty`, asserted via `pg_constraint.contype = 'c'`)
    **and** the UNIQUE constraint (`cyl_trait_sources_idempotency_key_key`, asserted via
    `contype = 'u'`). Asserting `contype`, not just the constraint *name*, is deliberate: the
    `_key`/`_nonempty` suffixes are convention, not enforcement — a name match alone wouldn't
    prove the UNIQUE is actually a UNIQUE. The CHECK guards the `""`-collision; the UNIQUE is the
    contract-level `1 ResultEnvelope : 1 source row` anchor — dropping it (keeping the CHECK)
    silently breaks idempotency, so both are active assertions.
  - **Contract sanity:** `contract_version` is in `$defs.Provenance.required` and typed `string`.
    `contract_version` is the per-row traceability anchor (which contract an envelope was produced
    under); the whole row-anchor story rests on the contract *guaranteeing* every envelope carries
    it. If sub-project #1 ever made it optional, D/G's future validation would have nothing to
    validate — so this structural tripwire is asserted now (no consumer needed). This assertion
    lives in the Node guard's `checkContractSanity` (fast lane) and is mirrored as a recorded note
    here; the *runtime* `provenance.contract_version` == `pin.json.version` validation is the
    deferred D/G hand-off below.
- **Deferred (skipped with a reason, not asserted):** B (`source_id` FK on the trait tables),
  C (intermediates/blob table), D (`idempotency_key == metadata->>'idempotency_key'` equality,
  RPC-enforced), `Provenance.contract_version` row-anchor validation (consumer-side, D/G), and
  `scan_key`→`cyl_scans.id` resolution. Marked `status="deferred"` so the file is a living
  extension point: each future change flips its row to active. This keeps the check meaningful
  today and **not brittle** against tables/columns/values that do not exist yet.

The deferred mapping rows are terse one-liners (`{..., status:"deferred", reason:"#295 source_id
FK"}`). The narrative hand-offs (remaining `Provenance` fields — including the reproducibility
anchors `inputs.images_checksum`/`image_ids` and `params.param_hash` — live *inside* the opaque
`metadata` jsonb, not promoted to columns; `contract_version`/`TraitValue.value` finiteness are
D/G consumer-side validations) live in `contracts/README.md` and the proposal's Cross-change
hand-offs, **not** duplicated as prose in the test file — the test carries mechanical deferred
rows only.

### D4 — CI wiring

- TS drift guard: a new step in `build-and-audit` → `node scripts/contract_types.mjs --check`
  (after `npm ci`). Pure Node, fast, no DB.
- Negative-path/`$id`-no-op test: a new step in `build-and-audit` →
  `node --test scripts/contract_types.test.mjs` (built-in runner, no config), no DB.
- Migration-match: no workflow change — placing the test under `tests/integration/` is sufficient
  (`compose-health-check` runs the whole directory).

## Risks / Trade-offs

- **json2ts CVE surface.** Adding a devDependency widens `npm audit --audit-level=critical`
  (already in `build-and-audit`). json2ts's tree is small/mainstream; no current criticals. →
  Accept; exact-pin keeps it explicit.
- **json2ts output stability across versions.** A json2ts upgrade could change formatting and
  trip the guard — which is *correct*: an upgrade is a deliberate `--write` + commit, gated by the
  exact pin. The risk is only an *unexpected* bump, which the exact pin + lockfile prevent.
- **Docstring tripwire brittleness — mitigated by vendoring.** Asserting `Provenance.description`
  contains `cyl_trait_sources.metadata` looks like a textbook brittle free-text assertion, but the
  schema is a **vendored, committed copy**: the assertion runs against the frozen file, so it
  *cannot* spontaneously fail on an upstream reword. It fires only when *we* re-vendor a changed
  schema — which is exactly the moment a human should re-confirm the home is still
  `cyl_trait_sources.metadata` (and update the one-line expected substring as part of that re-pin).
  So a hard failure is correct, and the "false alarm" mode (red build with no re-pin) cannot occur.
  This is the only contract-side net for a description-level re-home (json2ts drops descriptions,
  so the TS guard is blind to it).
- **Migration-match needs the compose stack.** It only runs in `compose-health-check`. → Acceptable:
  it reuses existing infra and asserts the real applied schema; the cheap TS guard + Vitest run in
  the fast Node job.

## Migration Plan

No database migration. Pure additive repo content (vendored schema, manifest, generated types,
guard script + Vitest, `.prettierignore`, one `.gitattributes` line, CI steps, pytest test) + one
devDependency. Rollback = revert the change; nothing is applied to any database. Re-pin procedure
(documented in `contracts/README.md`): replace the vendored schema, bump `pin.json.version`/`id`,
run `npm run contracts:gen`, commit; a `$id`-only diff regenerates identical types and all guards
pass; any real change produces a TS diff (caught by the guard) and a human reviews it.

## Open Questions

- None blocking. The cleaner long-term home for the migration-match requirement is the
  `cyl-trait-writeback` capability (where B/C/D already edit), but that capability is not a live
  spec on `staging` until change A's archive (#300) lands. Keeping all three requirements in the
  new `contract-pinning` capability now avoids coupling this change to #300; relocating req 3 is a
  deliberate later refactor.
