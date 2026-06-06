## Why

The sleap-roots pipeline produces per-scan results that must be written back into
Bloom **traceably** and **idempotently** (program sub-project #2; tracked under EPIC #9,
GitHub issue #13 "Results Sync", building on #12 "Metadata & Provenance"). The settled
contract (`sleap-roots-contracts`, `ResultEnvelope`) carries a `Provenance` envelope that
needs a home, and each pipeline run must map to **exactly one** `cyl_trait_sources` row so
that re-delivery of the same result is a no-op.

Today `cyl_trait_sources` is `(id, name)` only — it has no provenance and no idempotency
anchor, so trait values cannot be traced to the run that produced them and the same run
ingested twice would mint duplicate sources.

This is **change A** (the foundation) of sub-project #2. It only widens the source table;
the service-role write-back RPC that upserts on this key is a later change.

**Supersedes** the `cyl_trait_sources` schema proposed in issue #13 / `docs/issues/
issue-4-results-sync.md` (which added `pipeline_run_id UUID REFERENCES cyl_pipeline_runs`
+ a mutable `version` string and keyed source identity on `pipeline_run_id`). That design
predates the contract, depends on tables that do not exist (`cyl_pipeline_runs`,
`cyl_predictions`, `cyl_models`), and uses a mutable key. This change replaces it with the
contract's **deterministic, opaque `idempotency_key`** and stores the full run provenance
as `jsonb`, with no FK to an unbuilt table. Supersession is scoped to **#13's
`cyl_trait_sources` schema only** — #13's predictions table, Box backup, sync-status, and
trait-query API scope are untouched here. The contract's key-distinct-rows model also
replaces #13's `v1/v2` `version`-string UX: a re-run with new inputs/models/code yields a
different `idempotency_key` → a new source row (full history); same run re-delivered → same
key → no-op.

This is pipeline-side provenance (the #9/#12/#13 lineage). It does **not** address issue
#28 ("Add analysis provenance tracking"), which is the separate `sleap-roots-analyze`-side
provenance concern.

## What Changes

- ALTER `cyl_trait_sources`: add `metadata jsonb` (nullable) to hold the contract
  `Provenance` envelope. Stored as **opaque jsonb** — Bloom does not validate its shape at
  the DB layer (the contract validates it producer-side; change F adds a CI match check).
- ALTER `cyl_trait_sources`: add `idempotency_key text` (nullable) with:
  - a **UNIQUE** constraint — the anchor the write-back RPC upserts on. Nullable so
    existing/legacy `name`-only source rows coexist (Postgres permits multiple NULLs under
    UNIQUE);
  - a **CHECK** `idempotency_key IS NULL OR length(idempotency_key) > 0` — the contract
    defaults this field to `""` (empty string, *not* `NULL`); without this guard, the
    first `""` row would insert and every later keyless run would collide on the same
    `""`, and the write-back RPC's "unique-violation → no-op" path (change D) would return
    the **wrong `source_id`**, silently conflating distinct runs. The CHECK enforces the
    table's own invariant that `""` is not a valid key.
- The same `idempotency_key` value also appears nested inside the `metadata` envelope
  (`Provenance.idempotency_key`). It is **denormalized** into its own column so a UNIQUE
  constraint can anchor it; the write-back RPC (change D) is responsible for keeping the
  two consistent — they MUST NOT diverge. Enforcing that equality is **change D's** hard
  requirement (a generated column or cross-column CHECK here would break A's nullable /
  legacy-coexistence and opaque-jsonb design), not A's.
- Ship as a single **forward, additive** `supabase db push` migration (this repo is
  forward-only; see design.md), plus a companion manual rollback script under
  `supabase/rollbacks/`.
- Regenerate and commit the tracked Supabase TS types: `make gen-types` syncs **4**
  `database.types.ts` files (`packages/bloom-fs`, `packages/bloom-js`,
  `packages/bloom-nextjs-auth`, `web/lib`) that currently hardcode the old `(id, name)`
  shape. A 5th tracked copy, `web/types/database.types.ts`, is **not** written by
  `gen-types` and is an orphaned/stale legacy file (nothing imports
  `@/types/database.types`; the live import is `@/lib/database.types`) — update it by hand
  so the tracked types don't silently diverge.

Non-goals (separate changes): the write-back RPC (D), trait/blob row writes (B/C), the RLS
lockdown (E), and contract codegen / CI match (F). Empty-key rejection is enforced here at
the column level; **absent-key (NULL) detection** for RPC-written rows is the RPC's job.

## Impact

- Affected specs: `cyl-trait-writeback` (new capability)
- Affected code:
  - `supabase/migrations/` (one new forward migration) + `supabase/rollbacks/` (companion)
  - `tests/integration/` (new migration/constraint assertions, pytest)
  - 4 tracked `database.types.ts` files via `make gen-types` + `web/types/database.types.ts`
    by hand (gen-types does not write it)
- Affected issues: foundation under EPIC #9; supersedes #13's `cyl_trait_sources` schema
  only; depends conceptually on #12's provenance model; does not address #28 (analyze-side)
- Data: additive nullable columns + a UNIQUE constraint (multiple NULLs allowed) + a CHECK
  — safe on existing rows (all legacy sources keep `NULL` key/metadata; no backfill)
- **Interim exposure (sequencing risk):** `cyl_trait_sources` still carries a legacy
  permissive `authenticated` INSERT RLS policy (from its 2024 create migration), OR-combined
  with the 2026 `bloom_admin`/`bloom_user` role policies. Until **change E explicitly DROPs
  that legacy policy**, any authenticated user can write a row with a forged `metadata`
  envelope and a chosen `idempotency_key`. So the idempotency/provenance invariant is not
  fully DB-enforced until changes D + E land. This change records the window; it does not
  change RLS.
- **Cross-change ordering:** change **E** (DROP the legacy `authenticated` INSERT policy)
  MUST land **no later than change D** in any shared/deployed environment. The forgeable
  window only becomes exploitable once D's "unique-violation → no-op" RPC exists (a forged
  pre-claimed key is inert until something resolves traits against it), so E must not lag D
  into production.
