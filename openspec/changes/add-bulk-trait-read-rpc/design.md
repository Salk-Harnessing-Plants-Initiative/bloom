## Context

Roadmap Tier 1 (`bloommcp/docs/data-access-roadmap.md`, gated on A2 — nearly done, no longer blocking).
The shipped read surface (`add-cyl-trait-read-source-aware`, migration `20260701000000`) built:

- `cyl_scan_traits_source` — substrate view, one row per `cyl_scan_traits` row, exposing
  `scan_id, trait_id, trait_name, value, source_id, source_name, pipeline_run_id, is_latest`.
- `cyl_scan_traits_latest` — the `is_latest` rows.
- `get_scan_traits(experiment_id_ BIGINT, trait_name_ TEXT, source_id_ BIGINT DEFAULT NULL,
run_id_ TEXT DEFAULT NULL)` — one trait, one experiment, latest-by-default/pin-source/pin-run.

None of these fetch more than one trait per call. bloom#483's cylinder fixture (129 samples × 880 raw
trait columns / 123 × 649 post-QC) needs the whole trait set for one experiment in one round trip — the
concrete gap this change closes.

## Goals / Non-Goals

- **Goals:** one call returns every trait for one experiment in a single round trip; identical
  latest/source_id/run_id semantics to `get_scan_traits`, byte-for-byte on overlapping rows (reuse, not
  re-derivation, of `cyl_scan_traits_source`'s `is_latest` logic); a way to list an experiment's
  available sources before choosing one to pin; forward-only migration + rollback; no `bloommcp` code
  changes (Tier 2's job).
- **Non-Goals:** rewriting `SupabaseReader` (Tier 2); LLM-facing tool text, `BLOOM_TRAITS_DIR`/compose
  cleanup (Tier 3); any RLS or write-grant change; per-user auth (shared `bloom_agent` role stays,
  re-confirmed 2026-07-23).

## Decisions

### D1 — RPC shape: bulk long-format RPC (recommended) vs. PostgREST embedded-join query

**Status: open, gated on @blm3886 (Benfica)'s review before this migration ships** — bloom#546 names
this explicitly as needing her input, the same review gate as the shipped source-aware migration. This
proposal is written with a recommendation and a rejected alternative so it is reviewable as-is, per the
issue's own request to loop her in *before* the shape is treated as final — not because the choice is
ambiguous to the point of blocking a draft.

**Recommended: Option A — bulk long-format RPC**, `get_experiment_traits(experiment_id_ BIGINT,
source_id_ BIGINT DEFAULT NULL, run_id_ TEXT DEFAULT NULL)`. Same signature style as `get_scan_traits`
minus `trait_name_`, same result columns plus `trait_name`/`source_id` (a caller can no longer supply
the trait name, so it must come back in the row), same `RETURN QUERY` body reading
`cyl_scan_traits_source`, same leading `IF source_id_ IS NOT NULL AND run_id_ IS NOT NULL THEN RAISE
EXCEPTION` guard, same three-way disjunction:
```
WHERE cyl_experiments.id = experiment_id_ AND (
    (source_id_ IS NULL AND run_id_ IS NULL AND src.is_latest)
 OR (source_id_ IS NOT NULL AND src.source_id = source_id_)
 OR (run_id_ IS NOT NULL AND src.source_id = (
        SELECT max(s2.source_id) FROM public.cyl_scan_traits_source s2
        WHERE s2.scan_id = src.scan_id AND s2.trait_id = src.trait_id
          AND s2.pipeline_run_id = run_id_))
)
```
`LANGUAGE plpgsql STABLE SECURITY INVOKER`, same table-qualified `ORDER BY` discipline as
`get_scan_traits` (bare `plant_id` is ambiguous against the OUT column under `variable_conflict = error`).
Result: every `(scan, trait)` row for the experiment, long-format, one HTTP round trip. The bloommcp Tier
2 caller pivots long→wide itself — it already needs to, to do the canonical-role rename the roadmap's
column-role table specifies, so this is not new client-side cost.

Rationale for recommending A over B:

- **Validation.** The mutual-exclusion guard (`source_id_`/`run_id_` can't both be set) needs `RAISE`,
  which requires `plpgsql` — a view alone can't enforce it, so option B would need either three separate
  views (default/pin-source/pin-run) or push the guard to the client. A already has this guard, verified
  working, in `get_scan_traits`.
- **Round-trip count.** Both options return long-format rows in a single HTTP call — PostgREST does not
  pivot server-side, so a "PostgREST embedded-join query" against a new view is not inherently fewer
  round trips than an RPC call; the two approaches are equivalent on the oracle's core ask (single round
  trip). The distinguishing factor is call surface and validation ergonomics, not payload count.
- **Diff size.** A is a near-literal copy of `get_scan_traits` with one filter removed — minimal-diff,
  reviewable against a function Benfica already approved. B would introduce a new view shape and
  require re-deriving (or triplicating) the disjunction as view predicates.

**Rejected alternative: Option B — PostgREST embedded-join query.** Expose a
`cyl_experiment_traits_latest`-shaped view and have the bloommcp client call it via `.select()` with
embedded-resource expansion instead of `.rpc()`. Rejected for the reasons above (loses the built-in
guard; no round-trip advantage over A; would need multiple view variants to cover pin-source/pin-run
without the guard). Recorded here, not deleted, so the review has both options to weigh — **this is the
one point in the proposal Benfica's review can overturn without requiring a rewrite of the rest of the
design**, since D2–D5 below (grant spot-check, migration shape, testing) are independent of which shape
wins.

### D2 — `list_experiment_trait_sources` is a plain SQL function, not a view

`list_experiment_trait_sources(experiment_id_ BIGINT) RETURNS TABLE (source_id BIGINT, source_name TEXT,
pipeline_run_id TEXT)`. A function (not a view) because it's parameterized by `experiment_id_` — a view
would need the same `cyl_experiments.id = experiment_id_` predicate pushed down by the caller on every
query anyway, so a function makes the parameter explicit and mirrors `get_scan_traits`/
`get_experiment_traits`'s calling convention. `SELECT DISTINCT source_id, source_name, pipeline_run_id
FROM public.cyl_scan_traits_source src JOIN ... WHERE cyl_experiments.id = experiment_id_ AND
src.source_id IS NOT NULL` (excludes the legacy NULL-source placeholder — it is not a real, pinnable
source). `LANGUAGE sql STABLE SECURITY INVOKER` (no guard logic needed, so plain SQL suffices, unlike
D1's RPC).

### D3 — Grants: spot-check via test, no grant migration

The roadmap's Live-state facts (verified 2026-07-21, re-confirmed 2026-07-23) already traced
`bloom_agent`'s `SELECT` grants across all six join tables via `20260414002000_security_groups.sql`'s
schema-wide `GRANT SELECT ... TO bloom_agent` + matching per-table RLS `SELECT` policies
(`20260506000001_bloom_role_rls_policies.sql`). Both new functions are `SECURITY INVOKER` like
`get_scan_traits`, so they don't inherit a definer's privileges — confirming this at the role level is a
test (`SET LOCAL ROLE bloom_agent`, call both functions, assert no permission error), not a schema
change. This mirrors the source-aware migration's own D5/role-read tests rather than introducing a new
pattern.

### D4 — Same forward-only migration + rollback convention as the shipped precedent

Single migration file, `BEGIN; … COMMIT;`, dependency-ordered: (1) `get_experiment_traits` (2)
`list_experiment_trait_sources`. Both are pure additions (`CREATE FUNCTION`, no `DROP`/`CREATE OR
REPLACE` of anything existing) — unlike the source-aware migration, this one touches no pre-existing
object, so there is no drop-then-recreate step and no "does the grant survive DROP VIEW" concern (D5 in
the prior design). Companion rollback under `supabase/rollbacks/` simply `DROP FUNCTION IF EXISTS` both,
by full argument signature (to avoid ambiguity if a future change ever overloads them).

## Migration / Rollback

Single forward-only migration `supabase/migrations/<ts>_get_experiment_traits.sql` (timestamp later than
the most recent migration at merge time). Companion
`supabase/rollbacks/<ts>_get_experiment_traits_rollback.sql` drops both functions by full signature. All
five tracked `database.types.ts` copies are hand-edited to add the two new RPCs (mirroring the
source-aware precedent's `database.types.ts` update, since local dev-DB regeneration has a known gap —
CI's `compose-health-check` is the authoritative signal the migration applies).

## Risks / Trade-offs

- **D1 is explicitly not final** — if Benfica's review prefers Option B, this design and its tasks need
  a follow-up revision before merge. Flagged, not hidden: the rest of the proposal (D2–D4, testing plan)
  does not depend on which shape wins.
- **Payload size.** One `get_experiment_traits` call for the full raw cylinder fixture returns on the
  order of 10⁵ rows (129 samples × 880 traits ≈ 113k long-format rows; 123 × 649 post-QC ≈ 80k) in one
  response. Acceptable at this scale (a few MB of long-format rows); no pagination is added because the
  oracle explicitly requires a single round trip.
- **`list_experiment_trait_sources` excludes legacy NULL-source rows** — intentional (D2): there is
  nothing to "pin" for a placeholder that isn't a real source, so the listing only surfaces sources a
  caller could actually pass back into `source_id_`.

## Testing (TDD)

Integration tests in `tests/integration/test_cyl_experiment_traits.py`, following
`test_cyl_read_path.py`'s fixtures/helpers (`pg_conn`, `_seed_experiment_scan`, `_seed_two_sources`).
Oracle, mirrored from bloom#546 and the source-aware precedent's own scenario shape:

- One `get_experiment_traits` call returns every trait for a multi-scan, multi-trait experiment (no
  per-trait filter needed) — the round-trip-count assertion the whole change exists for.
- Default/pin-source/pin-run/both-set behavior on `get_experiment_traits` matches `get_scan_traits`
  row-for-row on overlapping `(scan, trait, source)` combinations (byte-for-byte per the roadmap's
  oracle) — build both from the same seeded fixture and diff.
- `list_experiment_trait_sources` lists each real source exactly once, excludes a legacy NULL-source
  scan, and returns nothing for an experiment with no sources.
- Role reads: `bloom_agent` can call both functions end-to-end through the full join chain (the D3
  spot-check).
- Forward migration is idempotent-safe on re-apply; rollback removes both functions cleanly.

Written RED first, confirmed failing (`UndefinedFunction`) before implementation.

## Open Questions

- **D1 (RPC shape) — blocking, awaiting @blm3886's review.** Everything else in this design is
  independent of the outcome.
