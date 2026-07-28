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
test, not a schema change. The spot-check SHALL cover all four read roles named by the "Bulk read grants"
requirement, matching the source-aware precedent's own role-read rigor (its "Read path stays open"
requirement tests the same four): `SET LOCAL ROLE bloom_agent`/`bloom_user`/`bloom_admin` and call both
functions directly; for `authenticated`, mirror the precedent's `test_authenticated_has_select_grant_on_views`
pattern — a bare `SET LOCAL ROLE authenticated` has no JWT/session context, so assert via
`has_function_privilege('authenticated', 'get_experiment_traits(bigint,bigint,text)', 'EXECUTE')` (and the
`list_experiment_trait_sources` equivalent) rather than attempting a role-assumed call.

### D4 — Same forward-only migration + rollback convention as the shipped precedent

Single migration file, `BEGIN; … COMMIT;`, dependency-ordered: (1) `get_experiment_traits` (2)
`list_experiment_trait_sources`. Both use `CREATE OR REPLACE FUNCTION` (safely re-runnable, matching the
idempotency test) even though neither replaces a pre-existing object — unlike the source-aware
migration, this one touches no pre-existing object, so there is no drop-then-recreate step and no "does
the grant survive DROP VIEW" concern (D5 in the prior design). Companion rollback under
`supabase/rollbacks/` simply `DROP FUNCTION IF EXISTS` both, by full argument signature (to avoid
ambiguity if a future change ever overloads them).

## Migration / Rollback

Single forward-only migration `supabase/migrations/<ts>_get_experiment_traits.sql`. The timestamp must be
later than every migration on **both** `main` and `staging` at the time the PR is opened — this repo's
own history has a real collision precedent (a same-day migration needed renaming after `staging` gained a
same-timestamp file first). Re-check immediately before opening the PR, and again before merge if the PR
sits open while other migrations land on `staging`. Companion
`supabase/rollbacks/<ts>_get_experiment_traits_rollback.sql` drops both functions by full signature. All
five tracked `database.types.ts` copies are hand-edited to add the two new RPCs (mirroring the
source-aware precedent's `database.types.ts` update, since local dev-DB regeneration has a known gap —
CI's `compose-health-check` is the authoritative signal the migration applies). **No TypeScript caller of
either new function exists anywhere in the repo** (unlike the source-aware precedent, where
`TraitExplorer.tsx`'s live 2-arg `get_scan_traits` call gave `tsc --noEmit` partial signal on the hand-edit)
— Tier 2 (the first caller) is explicitly deferred, so `tsc --noEmit` passing on this change proves nothing
about whether the hand-edited `Args`/`Returns` shape actually matches the migration. The hand-edit must be
checked by hand against the `RETURNS TABLE` clause, not treated as CI-verified.

## Risks / Trade-offs

- **D1 is explicitly not final** — if Benfica's review prefers Option B, this design and its tasks need
  a follow-up revision before merge. Flagged, not hidden: the rest of the proposal (D2–D4, testing plan)
  does not depend on which shape wins. If D1 flips **after** this migration has already merged and
  applied in any deployed environment, a plain `git revert` of the merge commit is not sufficient by
  itself: the correct sequence is (1) run
  `supabase/rollbacks/<ts>_get_experiment_traits_rollback.sql` against every environment the forward
  migration reached, (2) author a **new** forward migration implementing Option B (not a re-edit of the
  reverted one, per this repo's forward-only convention).
- **Payload size.** One `get_experiment_traits` call for the full raw cylinder fixture returns on the
  order of 10⁵ rows (129 samples × 880 traits ≈ 113k long-format rows; 123 × 649 post-QC ≈ 80k) in one
  response. Acceptable at this scale (a few MB of long-format rows); no pagination is added because the
  oracle explicitly requires a single round trip.
- **`list_experiment_trait_sources` excludes legacy NULL-source rows** — intentional (D2): there is
  nothing to "pin" for a placeholder that isn't a real source, so the listing only surfaces sources a
  caller could actually pass back into `source_id_`.

## Testing (TDD)

Integration tests in `tests/integration/test_cyl_experiment_traits.py`, following
`test_cyl_read_path.py`'s actual fixtures/helpers: `pg_conn`, `_seed_experiment_scan`, `_trait`, and
`_deliver(cur, img_ids, label, *, run=None, traits)` — `_deliver` already accepts a list of traits per
call (see `test_cyl_read_path.py`'s `test_no_cross_source_mixing`), so seeding multiple sources for one
scan is two `_deliver` calls against the same seeded scan (mirroring `test_two_sources_one_scan_seed`),
not a separate `_seed_two_sources` helper — no such helper exists.

**Byte-for-byte parity helper.** `get_experiment_traits` returns 11 columns (adds `trait_name`/`source_id`
since the caller no longer supplies a trait name) vs. `get_scan_traits`'s 10 — a direct row diff doesn't
type-check. Add `_assert_matches_get_scan_traits(cur, experiment_id, *, source_id=None, run_id=None)`:
group the bulk call's rows by `trait_name`, and for each group assert the `{(scan_id, trait_value)}` set
equals calling `get_scan_traits(experiment_id, trait_name, source_id_, run_id_)` for that trait — i.e.
compare per-trait row sets, not raw row lists, and exclude `source_id`/`trait_name` (the two columns
`get_scan_traits` doesn't return) from the comparison.

Oracle, mirrored from bloom#546 and the source-aware precedent's own scenario shape:

- One `get_experiment_traits` call returns every trait for a multi-scan, multi-trait experiment (no
  per-trait filter needed) — the round-trip-count assertion the whole change exists for.
- Default/pin-source/pin-run/both-set behavior on `get_experiment_traits` matches `get_scan_traits`
  byte-for-byte via `_assert_matches_get_scan_traits`, including the legacy NULL-source-scan case (the
  precedent's `test_legacy_null_source_scan_returned_by_default`) and cross-experiment isolation.
- `list_experiment_trait_sources` lists each real source exactly once (including one with a `NULL`
  `pipeline_run_id`, which is listed, not excluded — only a `NULL` `source_id` is excluded), excludes a
  legacy NULL-source scan, returns nothing for an experiment with only legacy data, and never leaks
  another experiment's sources.
- A dedicated no-write-capability test: static-scan the migration's SQL text for the absence of
  `CREATE POLICY`/`GRANT INSERT|UPDATE|DELETE|ALL`, distinct from the role-read test below.
- Role reads (D3): all four read roles (`bloom_agent`, `bloom_user`, `bloom_admin` via `SET LOCAL ROLE`;
  `authenticated` via `has_function_privilege`) can use both functions end-to-end through the full join
  chain, with no new grant on any table.
- Forward migration is idempotent-safe on re-apply, and re-applying does not alter `get_scan_traits` or
  the three existing views/functions; rollback removes exactly the two new functions and leaves every
  pre-existing read object unchanged.

Written RED first, confirmed failing (`UndefinedFunction`) before implementation.

## Open Questions

- **D1 (RPC shape) — blocking, awaiting @blm3886's review.** Everything else in this design is
  independent of the outcome.
