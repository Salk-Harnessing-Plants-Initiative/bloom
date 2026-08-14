## Why

[bloom#637](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/637) —
`list_experiments()` (`bloommcp/src/bloom_mcp/data_access/supabase_reader.py:247-311`) times out on
staging. Its one unpinned call to `get_experiment_summary_counts` (bloom#625) has two independent,
additive costs: (1) `cyl_scan_traits_source.is_latest` is a live `WindowAgg`
(`max(source_id) OVER (PARTITION BY scan_id)`) recomputed over the full 28.8M-row `cyl_scan_traits` table
on every read (~16.4s in isolation); (2) the surviving ~26M "latest" rows are then dragged through a
5-way join and `GROUP BY`, which alone exceeds the RPC's timeout budget.

This supersedes [PR #654](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/654), this
change's original implementation. That PR is fully built and tested but never merged — @blm3886
(Benfica) reviewed it and, working from real prod numbers (`cyl_scan_traits` = 28,786,885 rows but only
25,264 distinct `scan_id`s; only 9 of 269 experiments have any trait data), proposed a materially smaller
design for cost (1) in the PR's own comment thread, and filed
[bloom#656](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/656) identifying that cost
(2) is itself two different problems with two different fixes, one of which (`n_plants`) needs no cache
at all. This proposal adopts both, plus one correction this session's own testing found in Benfica's
comment (see `design.md`'s Context section) — a near-total redesign of PR #654's D1–D8, not an extension
of them.

## What Changes

- **`is_latest` moves off `cyl_scan_traits` entirely, onto a new one-row-per-scan table
  `cyl_scan_latest_source` (`scan_id` PK, `max_source_id`)** — 25,264 rows against today's data, not a
  boolean on all 28,786,885 trait rows. `cyl_scan_traits_source`'s view definition computes `is_latest`
  by joining to this table instead of a live window aggregate — same output, same partition grain, same
  NULL handling, verified byte-for-byte equivalent. No code change to `get_scan_traits`,
  `get_experiment_traits`, or `list_experiment_trait_sources` — they already read this view's `is_latest`
  column; only what computes it changes.
- **A trigger on `cyl_scan_traits` maintains the new table** for every write path (the write-back RPC and
  `bloom_admin`'s break-glass access), via a one-row `INSERT ... ON CONFLICT (scan_id) DO UPDATE` guarded
  by `pg_advisory_xact_lock(scan_id)`. The lock is a correction to Benfica's own PR #654 comment, which
  proposed dropping it — this session reproduced a real staleness race with the lock removed (two writers
  to the same new `scan_id` can converge to the wrong `max_source_id`) against a local Postgres, and
  confirmed the lock closes it. See `design.md` D2 for the reproduction.
- **The backfill is a single `INSERT ... SELECT ... GROUP BY`** (measured at 2,446ms on prod, cold cache
  by Benfica), run inside the same migration transaction as the schema and the view cutover — not a
  batched, resumable, operator-invoked procedure. This collapses PR #654's two-PR-plus-manual-runbook
  landing plan (and its entire D8 deploy-policy question) into one migration set, one PR. A `LOCK TABLE
  cyl_scan_traits IN SHARE MODE` held for the backfill's ~2.5s closes a gap this single-transaction
  approach would otherwise have (see `design.md` D3) — concurrent write-back calls briefly block, then
  proceed correctly; nothing is silently missed.
- **`get_experiment_summary_counts`'s unpinned path is rewritten in two halves, per bloom#656:**
  `n_plants` becomes a live `EXISTS` semi-join (247ms for all experiments, per Benfica's measurement) —
  **no cache at all**, since it needs no `is_latest` dependency (existence of any trait row for a scan
  implies existence of that scan's latest row). `n_traits` genuinely needs caching (6.6s, no shortcut) —
  a new `cyl_experiment_trait_counts` table, refreshed by a scheduled job (proposed default: a GitHub
  Action every 5–15 min, flagged for confirmation — see `design.md`'s Open Questions), not PR #654's
  per-row trigger (which would fire hundreds of full-experiment recomputes for one write-back upload).
- **The `source_id_`/`run_id_`-pinned branches keep a live join**, via a helper function scoped to just
  that case (simpler than PR #654's version, which also had to serve the unpinned path). Two incidental,
  semantics-preserving cleanups carried over from Benfica's comment: `JOIN accessions` → `accession_id IS
  NOT NULL`, and the unnecessary `cyl_experiments` join is dropped. **These branches' cost is reasoned
  about, not benchmarked** — no caller pins either parameter today, and this sandboxed environment can't
  run `EXPLAIN (ANALYZE, BUFFERS)` against staging; flagged as an explicit open item, matching bloom#656's
  own "unaddressed" flag on this exact question.
- **No change to `list_experiments()`'s Python code, its `ExperimentSummary` return shape, or any RLS
  policy/write grant.** Entirely a SQL/migration-layer change beneath the RPC that already exists.
- **Not a phased/multi-PR landing plan, unlike PR #654.** Nothing in this design requires a backfill slow
  enough to need batching, an operator runbook, or a gated cutover — see `design.md`'s Migration Plan.

## Impact

- Affected specs:
  - `cyl-trait-read` (existing capability, modified) — `is_latest`'s storage mechanism (view → join
    against a new table) and `get_experiment_summary_counts`'s unpinned read path (live semi-join +
    cache, not a rollup of both counts).
  - `cyl-trait-writeback` (existing capability, modified) — the new `cyl_scan_latest_source`-maintaining
    trigger and its inline backfill, replacing PR #654's `is_latest`-column trigger/backfill deltas
    (never merged).
  - `cyl-experiment-summary-rollup` (existing capability, modified) — narrowed from PR #654's
    `cyl_experiment_summary_counts` (both counts, per-row-trigger refresh) to `cyl_experiment_trait_counts`
    (`n_traits` only, scheduled refresh); `n_plants` is no longer part of this capability at all.
- Affected code:
  - `supabase/migrations/` (3 new migrations — see `design.md`'s Migration Plan) + `supabase/rollbacks/`
    companions.
  - A new scheduled job invoking `refresh_cyl_experiment_trait_counts()` — a GitHub Actions workflow file
    if D8's proposed default is confirmed, or a `workflows`-service follow-up issue if not.
  - `tests/integration/` — new/rewritten test files for the trigger, inline backfill, the semi-join
    rewrite, the cache table, and the rewritten `get_experiment_summary_counts`; no `bloommcp`/Python
    changes.
  - `bloommcp/docs/data-access-roadmap.md` / `_WIKI/BLOOMMCP/README.md` (docs only).
- Backward compatible: additive at the schema layer; the view's external contract and
  `get_experiment_summary_counts`'s signature/result shape are unchanged.
- Supersedes PR #654 entirely — that PR is closed, not merged, in favor of this proposal's own PR against
  the same change-id and the same underlying GitHub issue (bloom#637), folding in bloom#656.
