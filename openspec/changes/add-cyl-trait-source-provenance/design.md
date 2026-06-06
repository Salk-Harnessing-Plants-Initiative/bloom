## Context

`cyl_trait_sources` is a thin provenance dimension (`id BIGINT`, `name TEXT`) referenced
by `cyl_scan_traits.source_id` (already present) and — once change B lands —
`cyl_image_traits.source_id`. Sub-project #2 turns it into the **identity of one per-scan
pipeline run**: it must carry the run's full `Provenance` envelope and a unique key so the
write-back RPC can upsert idempotently.

Source of truth for the envelope shape is the contract:
`C:\repos\sleap-roots-contracts\schema\result_envelope.schema.json` (`$defs.Provenance`,
whose docstring states it *"serializes to cyl_trait_sources.metadata jsonb (sub-project
#2)"*). `Provenance.idempotency_key` is deterministic, computed producer-side from
`(scan_key, images_checksum, sorted model tuples, param_hash, predict_code_sha,
traits_code_sha)`; Bloom stores and compares it as an **opaque string** and never
recomputes it. Critically, the contract types it `string` with **`default: ""`** — an
unset key arrives as an empty string, never as SQL `NULL`.

## Goals / Non-Goals

- Goals: give `cyl_trait_sources` a `metadata jsonb` provenance column and a unique,
  non-empty `idempotency_key`; ship it as a forward additive migration with a manual
  rollback script; prove the columns + constraints with tests.
- Non-Goals: the write-back RPC, trait/blob writes, RLS changes, jsonb shape validation,
  contract codegen. No changes to existing rows or other tables.

## Decisions

- **Both new columns are nullable.** Existing `name`-only source rows (and any future
  non-pipeline source) have no envelope and no key. Postgres allows *multiple* NULLs under
  a UNIQUE constraint, so legacy rows coexist with pipeline rows without a backfill.
- **Plain `UNIQUE` constraint, not a partial index.** A nullable `UNIQUE` column already
  permits multiple NULLs, and the CHECK below handles the `""` case, so a partial index
  (`WHERE idempotency_key IS NOT NULL`) adds no integrity over `UNIQUE` + `CHECK` while
  forcing change D's RPC to restate the predicate in `ON CONFLICT`. The plain constraint
  is targetable by `ON CONFLICT (idempotency_key)` directly. The UNIQUE constraint also
  provides the lookup index the RPC's upsert needs — no separate index.
- **CHECK rejects the empty-string sentinel at the DB layer.** `CHECK (idempotency_key IS
  NULL OR length(idempotency_key) > 0)`. This is **not** coupling the migration to producer
  behavior — it enforces the table's own invariant that `""` is not a valid idempotency
  key. Rationale (defense in depth for a provenance anchor): the contract's `default: ""`
  means a producer that fails to compute a key emits `""`, not `NULL`. Without the CHECK,
  the first `""` row inserts, then every subsequent keyless run collides on UNIQUE — and
  change D's planned "unique-violation → idempotent no-op" would return the *existing*
  `source_id`, silently binding a distinct run's traits to the wrong provenance. A CHECK
  turns that silent semantic corruption into an immediate, loud violation, costs nothing,
  and stays additive/rollback-safe (all existing values are NULL, which the CHECK permits).
  Boundary: the CHECK guards the empty string `''` only. A whitespace-only key (`'   '`)
  passes `length > 0` and is **permitted** — a legitimately-computed key is a sha256-derived
  hash and can never be whitespace, so blank-but-nonempty keys are the producer's
  responsibility (caught producer-side by the contract and by change F's CI match), not a
  DB invariant. The CHECK deliberately rejects, never rewrites/normalizes, an opaque anchor.
- **`metadata` is stored as opaque jsonb, not validated at the DB layer.** Envelope
  validity is guaranteed producer-side by the Pydantic contract and (change F) by a CI
  migration/types-match check. A DB-side jsonb schema check is out of scope and would
  duplicate the contract.
- **`idempotency_key` is denormalized.** The same value lives both in its own column (so a
  UNIQUE constraint can anchor it) and nested in `metadata->>'idempotency_key'`. The
  write-back RPC (change D) MUST keep them consistent; they must not diverge. Documented
  here so an implementer doesn't treat the column and the envelope field as independent.
- **No GIN/expression index on `metadata` yet (YAGNI).** No query path filters on metadata
  in #2. This change forecloses nothing: an expression index or `GENERATED` column on
  `metadata->>'contract_version'` (to find rows under a stale contract) or
  `metadata->>'scan_key'` (to audit source→scan resolution) can be added non-destructively
  later if an audit need appears.

## Risks / Trade-offs

- **Nullable `idempotency_key` → an RPC bug could write a NULL-key row** that passes all
  constraints yet is permanently untraceable to its run (distinct from the `""` collision:
  this is the NULL-escape). A cannot fix it (nullable is required for legacy coexistence).
  Invariant recorded for changes D/E: every row written through the sanctioned RPC carries
  a non-empty key, ideally validated by the RPC and by a periodic "sources missing a key"
  audit query.
- **Forgeable-provenance window until change E.** `cyl_trait_sources` still has a legacy
  `CREATE POLICY ... FOR INSERT TO authenticated WITH CHECK (true)` (from its 2024 create
  migration), OR-combined with the 2026 role policies. Any authenticated user can therefore
  INSERT a forged `metadata`/`idempotency_key` row — and pre-claim a run's key, making the
  RPC's no-op bind real traits to attacker/buggy-client provenance. **Change E must DROP
  that legacy policy** (not merely add role policies). Recorded so it cannot fall through.
- A unique violation surfaces to the RPC as a Postgres error (SQLSTATE 23505) → change D
  must use the catch-23505-and-return-existing pattern (cf. gravi #252, the RPC-side
  catch-23505 fix), not SELECT-then-INSERT, to make re-delivery a clean no-op. The
  NULL-escape risk below is the same failure mode as gravi #250 (UNIQUE treats NULLs as
  distinct, so NULL keys do not collide). (#251 was a *client-side* bloom-js upsert fix, a
  different pattern — not the model for D's RPC.)

## Migration Plan

This repo is **forward-only**: migrations are applied with `supabase db push`
(`make migrate-local`, `Makefile`; CI `pr-checks.yml` `compose-health-check`). There is no
down-runner; "reversibility" is a **separate, manual** script under `supabase/rollbacks/`
(wrapped in `BEGIN; … COMMIT;`, `DROP … IF EXISTS`), never applied by CI.

- Forward migration (`make new-migration name=add_cyl_trait_source_provenance`):
  - `ALTER TABLE cyl_trait_sources ADD COLUMN metadata jsonb;`
  - `ALTER TABLE cyl_trait_sources ADD COLUMN idempotency_key text;`
  - `ALTER TABLE cyl_trait_sources ADD CONSTRAINT cyl_trait_sources_idempotency_key_key UNIQUE (idempotency_key);`
  - `ALTER TABLE cyl_trait_sources ADD CONSTRAINT cyl_trait_sources_idempotency_key_nonempty CHECK (idempotency_key IS NULL OR length(idempotency_key) > 0);`
  - Additive only — no drops/rewrites, safe to `db push` once onto the persistent DB; the
    timestamp prefix must exceed the current max (`scripts/lint_migrations.sh`).
- Companion rollback (`supabase/rollbacks/<version>_add_cyl_trait_source_provenance_rollback.sql`),
  manual: `BEGIN;` drop both constraints `IF EXISTS`, drop both columns `IF EXISTS`; `COMMIT;`.
- Apply + reset locally only (`make migrate-local` / `supabase db reset`), never prod.
- Regenerate TS types (`make gen-types`, which syncs 4 `database.types.ts` files) and
  manually update the orphaned 5th (`web/types/database.types.ts`); commit all.

## Open Questions

- None blocking. (The `scan_key → cyl_scans.id` resolution is settled for later changes:
  resolve via `Provenance.inputs.image_ids → cyl_images.scan_id`, caller-side; it does not
  affect this column-only change.)
