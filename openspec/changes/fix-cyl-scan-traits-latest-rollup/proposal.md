## Why

[bloom#637](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/637) —
`list_experiments()` (`bloommcp/src/bloom_mcp/data_access/supabase_reader.py:247-311`) times out on
staging. Its one unpinned call to `get_experiment_summary_counts` (bloom#625, merged and archived —
[2026-08-12-fix-bloommcp-list-experiments-summary-rpc](../archive/2026-08-12-fix-bloommcp-list-experiments-summary-rpc/))
has two independent, additive costs, both confirmed via `EXPLAIN (ANALYZE, BUFFERS, TIMING)` against
staging: (1) `cyl_scan_traits_source.is_latest` is a live `WindowAgg`
(`max(source_id) OVER (PARTITION BY scan_id)`) recomputed over the full 28.8M-row `cyl_scan_traits` table
on every read (~16.4s in isolation) because it lives on a view, not a stored column — there is nothing to
index; (2) the surviving ~26M "latest" rows are then dragged through a 5-way join
(`cyl_experiments → cyl_waves → cyl_plants → accessions → cyl_scans → cyl_scan_traits_source`) and a
`GROUP BY`, which alone exceeds the RPC's 90s-effective timeout budget. `get_experiment_traits`,
`get_scan_traits`, and `list_experiment_trait_sources` all pin one `experiment_id_`, so they're believed
safe from cost (2) — but this was never benchmarked against `experiment_id=1` specifically (13.8M of
28.8M rows), which bloom#625's own task 0.2 left explicitly open and this change supersedes (see that
task's note, and cost (1) still affects every one of those pinned callers today).

@blm3886 (Benfica) resolved the direction on the issue (2026-08-09): move `is_latest` onto
`cyl_scan_traits` as a real, stored, indexed column, maintained by a trigger covering every write path
(not RPC-embedded logic, so corrections and any future ingest path are covered too); then, once that
makes the aggregate cheap enough to actually run, add a per-experiment `(experiment_id, n_plants,
n_traits)` rollup table for `list_experiments()` to read directly — no live join, no window function.
Step 2 is explicitly gated on step 1: refreshing the rollup means re-running the aggregate, which is only
cheap once `is_latest` is indexed.

## What Changes

- **`cyl_scan_traits` gains a real, stored, `NOT NULL` `is_latest boolean DEFAULT false` column**,
  indexed for per-scan/per-experiment lookups, preserving the view's exact current semantics — `is_latest`
  is true iff a row's `source_id IS NOT DISTINCT FROM max(source_id)` **within that row's `scan_id`**
  (unchanged partition grain; a research pass initially proposed re-partitioning by
  `(scan_id, trait_id)` as a "bug fix," but `tests/integration/test_cyl_read_path.py:284`'s
  `test_no_cross_source_mixing` and the live `cyl-trait-read` spec's own "no backfill from an older
  source" scenario confirm the current per-`scan_id` grain is intentional, tested behavior — corrected
  before this proposal was written, not carried through).
- **An `AFTER INSERT OR UPDATE OR DELETE` trigger on `cyl_scan_traits` maintains `is_latest`** for every
  write to the table, regardless of caller — covering `insert_cyl_result_envelope` (the sole sanctioned
  ingest/rerun/write-back RPC path) and `bloom_admin`'s break-glass direct-table access (the only other
  live write surface; there is no separate "corrections" application code today). A trigger on the table
  itself, not RPC-embedded logic, is what makes this true regardless of how a row arrives. (`AFTER`, not
  `BEFORE` — a `BEFORE DELETE` trigger would still see the about-to-be-deleted row when recomputing
  `max(source_id)`, which would break the "deletion promotes the next-highest source" behavior; an
  earlier draft of this section said `BEFORE`, corrected here to match design.md D2's actual SQL.)
- **A one-time, batched backfill** (a `CALL`-able procedure, run outside the schema migration's own
  transaction) sets `is_latest` correctly for all ~28.8M pre-existing rows, scoped by `scan_id` ranges so
  no single transaction holds a long-running lock. The view keeps reading the **live** `WindowAgg`
  computation until the backfill is verified complete — only then does a follow-up migration cut
  `cyl_scan_traits_source.is_latest` over to read the stored column. This sequencing means there is no
  window where a reader could see an under-populated `is_latest` value. **Running this procedure requires
  a direct `psql` connection — PostgREST cannot invoke `CALL`** — which this repo's own deploy-migration
  policy currently scopes to documented emergency recovery, not routine feature deploys; see design.md D8
  and this proposal's Impact section for how this change resolves that conflict rather than glossing over
  it.
- **A new `cyl_experiment_summary_counts` rollup table** (`experiment_id` PK, `n_plants`, `n_traits`),
  populated by a one-time backfill and kept current by an **event-driven update scoped to the one
  experiment whose write just landed**, piggybacking on the same is_latest-maintaining trigger (resolving
  every affected `scan_id` to its owning `experiment_id` and re-running that one experiment's aggregate).
  **Benfica's comment says only that the rollup is "kept current on a refresh," without naming the
  mechanism** — this proposal picks event-driven/per-experiment rather than a scheduled whole-table
  refresh (matching this repo's own precedent of shipping a considered default and flagging it for
  reviewer confirmation rather than blocking on a round-trip — see design.md's Open Questions) and flags
  it explicitly for her confirmation during review.
- **`get_experiment_summary_counts` is rewritten**: when `source_id_`/`run_id_` are both `NULL` (the
  "current latest" case — pinned to one experiment or not), it now reads directly from
  `cyl_experiment_summary_counts` instead of doing the live join — this covers `list_experiments()`'s
  bulk case and, incidentally, any future single-experiment pinned call with no source/run override. The
  `source_id_`/`run_id_`-pinned branches (unused today, reserved for a future source-selection effort)
  keep the live join but have their `COUNT(DISTINCT ...)` rewritten as a `GROUP BY` subquery per
  Benfica's "additionally" note, avoiding a large per-experiment sort — this also benefits the rollup's
  own per-experiment refresh query, which reuses the same aggregation.
- **No change to `list_experiments()`'s Python code, its `ExperimentSummary` return shape, or any RLS
  policy/write grant.** This change is entirely in the SQL/migration layer beneath the RPC that already
  exists. Three new `SECURITY DEFINER` objects are added (the D2 trigger function, the D6 rollup-refresh
  function, and a D7 helper both of those call) — none widens any role's *effective* privilege given
  today's writers (`postgres` via the write-back RPC, `bloom_admin`, `bloom_writer` all already have
  equal-or-greater access), but this is a real increase in `SECURITY DEFINER` surface, not a no-op, and
  is called out explicitly here rather than only in design.md's fine print.
- **BREAKING (deploy sequencing, not API):** this change lands as **two PRs against the same OpenSpec
  change**, not one — see Impact below. The first PR's migrations are inert until an operator runs two
  manual backfills on staging; the second PR (view cutover + RPC rewrite) must not merge until those
  backfills are verified complete. This split exists because staging's deploy workflow
  (`.github/workflows/deploy.yml`) runs `supabase db push` unconditionally over every pending migration
  on merge, with no gate for a human step in between — shipping the cutover/rewrite migrations in the
  same merge as their prerequisite backfill would auto-deploy before the backfill runs, which would make
  every reader see zero traits/plants (worse than today's timeout, not a lateral improvement).

## Impact

- Affected specs:
  - `cyl-trait-read` (existing capability, modified) — `is_latest`'s storage mechanism (view → stored
    column) and `get_experiment_summary_counts`'s unpinned/rollup-backed read path.
  - `cyl-trait-writeback` (existing capability, added) — the new `is_latest`-maintaining trigger and its
    one-time backfill, as a write-path table-maintenance concern.
  - `cyl-experiment-summary-rollup` (new capability) — the rollup table's shape, backfill, and
    event-driven maintenance.
- **Landing plan — two PRs against this one OpenSpec change, per design.md's Migration Plan:**
  - **Phase 1 PR** (opens now): schema (`is_latest` column, trigger, index), the backfill procedure
    definition, and the rollup table + refresh function/trigger wiring. All additive/inert — nothing
    reads the new column or table yet, so this is safe to auto-deploy the moment it merges.
  - **Operator runbook, between the two PRs**: run the `is_latest` backfill, verify it (zero mismatches
    against the live computation), run the rollup backfill — see design.md D8 for the connection
    mechanism this requires and the open policy question it raises.
  - **Phase 2 PR** (code-only, same change, opens after the runbook above is complete): the view cutover
    and the `get_experiment_summary_counts` rewrite. Merging this before the runbook completes is the
    exact failure mode this split exists to prevent.
- Affected code:
  - `supabase/migrations/` (5 new, ordered migrations across the two phases — see design.md's Migration
    Plan) + `supabase/rollbacks/` companions. Whether the rollup table needs an entry in the five tracked
    `database.types.ts` copies is **not yet resolved** (no TS caller exists today, but `supabase gen
    types`'s actual output against a migrated local DB hasn't been checked) — see design.md's Open
    Questions; tasks.md carries an explicit task to resolve this before Phase 1 merges, not an assumption
    either way.
  - `tests/integration/` — new test files for the trigger, backfill procedure, rollup table, and the
    rewritten `get_experiment_summary_counts`; no `bloommcp`/Python changes, since `list_experiments()`
    already calls this RPC unpinned today and its contract is unchanged.
  - `bloommcp/docs/data-access-roadmap.md` / `_WIKI/BLOOMMCP/README.md` (docs only, noting the storage
    change and flagging the rollup-refresh-mechanism question for Benfica).
- Backward compatible: additive at the schema layer (new column, new table, new trigger); the view's
  external contract (`cyl_scan_traits_source`/`cyl_scan_traits_latest`'s columns and values) and
  `get_experiment_summary_counts`'s signature and result shape are unchanged — only the query paths
  underneath both get faster.
- Supersedes [bloom#625](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/625)'s task
  0.2 (benchmark `experiment_id=1` to size the RPC timeout below its 120s interim default) — see that
  task's note in the archived change's `tasks.md`, marked superseded rather than left open indefinitely.
