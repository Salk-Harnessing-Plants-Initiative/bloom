## Context

[bloom#637](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/637) —
`list_experiments()` times out on staging. The unpinned call to `get_experiment_summary_counts`
(bloom#625) has two independent, additive costs: (1) `cyl_scan_traits_source.is_latest` is a live
`WindowAgg` (`max(source_id) OVER (PARTITION BY scan_id)`) recomputed over the full 28.8M-row
`cyl_scan_traits` table on every read; (2) the surviving ~26M "latest" rows are then dragged through a
5-way join and `GROUP BY`.

This change was originally drafted as [PR #654](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/654):
store `is_latest` as a real boolean column on all 28.8M `cyl_scan_traits` rows, trigger-maintained, plus
a per-experiment `(n_plants, n_traits)` rollup table refreshed per-row off the same trigger. That PR is
superseded by this revision, not extended, for two reasons:

**Benfica's review on PR #654** (real prod numbers, not estimates: `cyl_scan_traits` = 28,786,885 rows,
but only 25,264 distinct `scan_id`s; only 9 of 269 experiments have any trait data): a boolean on every
trait row stores the same fact 1,139x more times than necessary. Her proposal — `cyl_scan_latest_source`,
one row per scan (`scan_id` PK, `max_source_id`) — holds the same information at table scale, not row
scale, and its trigger writes to a *different* table, so it never re-fires itself (no recursion guard
needed) and its one-time backfill is a single `INSERT ... SELECT ... GROUP BY` measured at **2,446ms on
prod, cold cache** — cheap enough to run inside the migration itself, not as a batched, resumable,
operator-run procedure gated behind a deploy-policy exception (PR #654's D8).

**[bloom#656](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/656)**, filed against
PR #654 in parallel: the query *shape* is a second, independent bottleneck. `n_plants` via
`COUNT(DISTINCT ...)` costs 16,520ms for one experiment — 12,927ms of that is dragging 13.8M matched rows
through the join before deduplicating, not the join itself (60ms). Rewritten as a semi-join (`EXISTS`),
all experiments at once cost 247ms — **no cache needed at all**. `n_traits` genuinely needs a full scan
(6,623ms) and does need caching, but PR #654's per-row `AFTER` trigger is the wrong refresh shape: one
write-back upload inserts ~751 trait rows in a loop, so a per-row trigger fires ~751 full-experiment
recomputes for one upload. A scheduled refresh (every 5–15 min) costs the same 6.6s regardless of
ingest volume, and runs where nothing is waiting on it.

Between them, these two reviews eliminate essentially all of PR #654's operational complexity: no
batched/resumable backfill, no operator runbook, no D8 deploy-policy carve-out, no three-way
Phase-1/Phase-2/Phase-3 migration sequencing. This design lands as **one migration set, one PR**.

**A correction this design makes to Benfica's own PR #654 comment, found by testing it rather than
reviewing it by eye:** her proposed trigger (`INSERT ... ON CONFLICT (scan_id) DO UPDATE SET
max_source_id = EXCLUDED.max_source_id`, no advisory lock — "ON CONFLICT handles two writers hitting
the same scan") does not actually hold under a genuine concurrent-write race. Reproduced empirically
against a local Postgres (see D2): two connections racing to upsert the same new `scan_id` can converge
to the **wrong** `max_source_id` — `EXCLUDED` is fixed at proposal time, before the conflict wait
completes, and (also verified empirically) replacing it with a fresh correlated subquery in the `SET`
clause does not fix this either, because Postgres fixes one snapshot for the whole statement before the
lock-wait, not after. `pg_advisory_xact_lock(scan_id)` — the same fix PR #654's own testing already found
necessary for its column-based design — closes it here too, confirmed by rerunning the identical race
with the lock added. The size win (25,264 rows instead of 28,786,885) is real and kept; the "no lock
needed" claim is not, and this design does not carry it forward uncorrected.

## Goals / Non-Goals

- **Goals:** `list_experiments()` (and any future unpinned or pinned-no-override caller) returns in well
  under a second at staging's real scale. `is_latest` is derived from a stored, indexed, per-scan table
  whose value never disagrees with a fresh `max(source_id)` computation, for any scan, at any time —
  verified by tests, not assumed. Every live write path to `cyl_scan_traits` (the write-back RPC and
  `bloom_admin`'s break-glass access) keeps it correct without relying on the writer to know about it.
  `n_plants` is correct and live for every call (no staleness). `n_traits` is cheap to read, with an
  explicit, bounded, documented staleness window (one refresh interval) that is a UI-lag tradeoff, not a
  data-integrity one — the underlying trait data itself is never inconsistent, only this one summary
  count can lag.
- **Non-Goals:** re-deriving or changing `is_latest`'s selection semantics (per-`scan_id` partition grain,
  `IS NOT DISTINCT FROM` legacy-NULL handling — unchanged from the live `cyl-trait-read` spec). No new MCP
  tool, no `source_id_`/`run_id_` parameter threaded through any analysis tool. No change to
  `get_experiment_traits`, `get_scan_traits`, or `list_experiment_trait_sources`'s own signatures — they
  read `cyl_scan_traits_source.is_latest` exactly as before; only what's underneath that column changes.
  No RLS change. Real-time (sub-refresh-interval) freshness for `n_traits` — that tradeoff is deliberate
  (see D5), not an oversight.

## Decisions

### D1 — `cyl_scan_latest_source`: one row per scan, not a boolean per trait row

```sql
CREATE TABLE public.cyl_scan_latest_source (
    scan_id       bigint PRIMARY KEY REFERENCES public.cyl_scans(id) ON DELETE CASCADE,
    max_source_id bigint
);
```

25,264 rows against today's data, not 28,786,885. `max_source_id` is nullable — `cyl_scan_traits.source_id`
itself is nullable for legacy pre-source-tracking rows, and the "all rows for a scan are legacy NULL"
case must still resolve to `is_latest = true` for those rows (unchanged rule, see D3).

**Why this replaces a stored boolean column entirely, not just its backfill:** the boolean design (PR
#654 D1–D4) needed a batched, resumable backfill procedure specifically because it had to touch all
28.8M existing rows without holding one transaction open for the duration. A table scoped to *scans*
(25,264 of them) needs no such batching — its backfill is one aggregate query (D3).

### D2 — Trigger: per-row upsert, guarded by an advisory lock (verified necessary, not assumed away)

```sql
CREATE OR REPLACE FUNCTION public.maintain_cyl_scan_latest_source()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    affected_scan_id bigint := COALESCE(NEW.scan_id, OLD.scan_id);
BEGIN
    -- Serializes concurrent writers to the SAME scan_id — see the Context section's empirical
    -- finding. Scoped to one scan_id, so this never contends with a write to a different scan.
    PERFORM pg_advisory_xact_lock(affected_scan_id);

    INSERT INTO public.cyl_scan_latest_source (scan_id, max_source_id)
    SELECT affected_scan_id, max(source_id)
    FROM public.cyl_scan_traits
    WHERE scan_id = affected_scan_id
    ON CONFLICT (scan_id) DO UPDATE SET max_source_id = EXCLUDED.max_source_id;

    RETURN NULL;  -- AFTER trigger; return value is ignored
END;
$$;

CREATE TRIGGER maintain_cyl_scan_latest_source_after_write
    AFTER INSERT OR UPDATE OR DELETE ON public.cyl_scan_traits
    FOR EACH ROW
    EXECUTE FUNCTION public.maintain_cyl_scan_latest_source();
```

**Writes to a different table than the one the trigger is on** — this is what makes the trigger
non-recursive by construction (PR #654's design needed an `IS DISTINCT FROM` guard specifically because
its maintaining `UPDATE` targeted `cyl_scan_traits` itself, re-firing its own trigger; this trigger's
`INSERT`/`UPDATE` targets `cyl_scan_latest_source`, which has no trigger of its own, so there is nothing
to recurse into). This part of Benfica's simplification holds and is kept as-is.

**Why the advisory lock, empirically, not just defensively:** reproduced against local Postgres —
connection B inserts a trait row (`source_id=6`, the true eventual max) and upserts (uncommitted);
connection A inserts a trait row for the *same new* `scan_id` (`source_id=5`), then attempts its own
upsert, which blocks on B's uncommitted row; B commits; A's blocked upsert unblocks and applies
`SET max_source_id = EXCLUDED.max_source_id` using the value A computed *before* the wait (`5`, since A
could not see B's uncommitted row when it computed its own proposed value) — final state `5`, not the
correct `6`. Confirmed this is not fixed by using a fresh correlated subquery in the `SET` clause instead
of `EXCLUDED` either (same wrong result) — Postgres fixes one snapshot for the whole statement at
statement start, before the conflict wait, regardless of where in the statement a subquery sits. Adding
`pg_advisory_xact_lock(affected_scan_id)` *before* the read-then-upsert and rerunning the identical race
produced the correct result (`6`): the lock forces A's entire read-then-write to happen as a fresh
statement *after* B's commit, with a fresh snapshot that sees it. This covers both the shape PR #654's
own testing already found (two writers racing a *pre-existing* scan) and the brand-new-scan variant,
uniformly, since the lock serializes on `scan_id` regardless of whether a row already exists.

**`SECURITY DEFINER` necessity — same reasoning as PR #654's D2, unchanged:** every role that can write
`cyl_scan_traits` today (`postgres` via the write-back RPC, `bloom_admin`, `bloom_writer`) already has
privilege equal to or exceeding what this trigger's own maintenance write needs as `SECURITY INVOKER`, so
`SECURITY DEFINER` is not functionally required by any writer that exists today — kept defensively, in
case a future narrowly-scoped writer role would otherwise be blocked from maintaining
`cyl_scan_latest_source` by its own RLS.

**Per-row granularity matches how writes already happen**, same reasoning as PR #654's D2:
`insert_cyl_result_envelope` inserts one row per trait per `INSERT` in a loop, so a per-row trigger fires
once per trait — but unlike PR #654's design, each firing here only ever touches *this one scan's* small
table row (an `O(1)` upsert, not a table-wide recompute), so firing 751 times for one envelope costs 751
cheap upserts, not 751 full-experiment aggregations. This is the concrete sense in which this design
avoids bloom#656's per-row-trigger objection without needing a scheduled refresh for `is_latest` itself
— only `n_traits` (D5) needs one.

### D3 — Single migration: schema, trigger, inline backfill, and view cutover together

**No phased Phase 1/Phase 2 split, unlike PR #654.** That split existed because PR #654's backfill was a
batched, potentially long-running, operator-invoked procedure — cutting the view over before it finished
would have meant readers seeing `is_latest = false` (the column's default) for any not-yet-backfilled row.
This design's backfill is a single aggregate query over `cyl_scan_traits`, measured at 2,446ms on prod —
short enough to run inside the same migration transaction as the schema and the view cutover, with no
separate operator step and no runbook.

**The one new subtlety a single-transaction backfill introduces, and how it's closed:** between the
moment `CREATE TRIGGER` takes effect and the moment the backfill's `SELECT` executes, a *concurrent*
write transaction that began before this migration's DDL is visible to it (i.e., before this migration
commits) would see neither the new trigger (its own snapshot predates the DDL) nor get captured by the
backfill (if it commits after the backfill's `SELECT` already ran) — a scan could fall into a gap where
nothing populates its `cyl_scan_latest_source` row. This is closed by taking a table-level lock that
blocks concurrent writers (but not readers) for the ~2.5s backfill:

```sql
BEGIN;

-- 1. Schema (D1) + trigger (D2) — CREATE TRIGGER itself briefly takes ACCESS EXCLUSIVE on
--    cyl_scan_traits, same as any DDL touching an existing table; catalog-only, sub-millisecond.
CREATE TABLE public.cyl_scan_latest_source ( ... );
CREATE FUNCTION public.maintain_cyl_scan_latest_source() ...;
CREATE TRIGGER maintain_cyl_scan_latest_source_after_write ...;

-- 2. Block concurrent WRITERS (not readers — SHARE MODE conflicts with ROW EXCLUSIVE, which
--    INSERT/UPDATE/DELETE need, but not with ACCESS SHARE, which SELECT needs) for the
--    remainder of this transaction. Any write-back RPC call that lands during the backfill
--    below simply waits the ~2.5s for this transaction to commit, then proceeds normally,
--    now seeing the trigger created in step 1 and computing correctly against a snapshot that
--    includes the backfill's own committed data.
LOCK TABLE public.cyl_scan_traits IN SHARE MODE;

-- 3. Backfill — one aggregate pass, not batched. ON CONFLICT makes this migration body
--    idempotent (safe to re-run), matching this repo's existing migration-idempotency test
--    convention.
INSERT INTO public.cyl_scan_latest_source (scan_id, max_source_id)
SELECT scan_id, max(source_id) FROM public.cyl_scan_traits GROUP BY scan_id
ON CONFLICT (scan_id) DO UPDATE SET max_source_id = EXCLUDED.max_source_id;

-- 4. View cutover — safe in the same transaction specifically because step 2's lock
--    guarantees the backfill is complete and no writer landed data this transaction can't
--    see by the time this commits.
CREATE OR REPLACE VIEW public.cyl_scan_traits_source ... -- see below

COMMIT;
```

```sql
CREATE OR REPLACE VIEW public.cyl_scan_traits_source
WITH (security_invoker = on) AS
SELECT
    cst.scan_id,
    cst.trait_id,
    t.name                                AS trait_name,
    cst.value,
    cst.source_id,
    s.name                                AS source_name,
    s.metadata ->> 'pipeline_run_id'      AS pipeline_run_id,
    (cst.source_id IS NOT DISTINCT FROM l.max_source_id) AS is_latest
FROM public.cyl_scan_traits cst
JOIN public.cyl_scan_latest_source l ON l.scan_id = cst.scan_id
LEFT JOIN public.cyl_trait_sources s ON s.id = cst.source_id
LEFT JOIN public.cyl_traits       t ON t.id = cst.trait_id;
```

The `JOIN` to `cyl_scan_latest_source` is a plain equi-join on `scan_id` (already indexed —
`idx_cyl_scan_traits ON cyl_scan_traits(scan_id)`, `20240828142957`), not a per-row window aggregate —
this is what removes the ~16.4s `WindowAgg` cost. **No new index is needed on `cyl_scan_traits` for this
join** (unlike bloom#656's suggested `idx_cyl_scan_traits_latest` partial index, which assumed a stored
per-row boolean — not applicable to a join-based `is_latest`). Every row of `cyl_scan_traits` has exactly
one matching `cyl_scan_latest_source` row by construction (the trigger creates one on the first write to
any `scan_id`, the backfill covers every pre-existing one), so the inner join never silently drops rows.

**Output is byte-for-byte identical to the live `WindowAgg`** — same partition grain (`scan_id`), same
`IS NOT DISTINCT FROM` NULL handling (a scan with only legacy NULL-`source_id` rows gets
`max_source_id = NULL`, and `NULL IS NOT DISTINCT FROM NULL = true`, matching the "legacy NULL rows count
as latest" rule unchanged from the live spec). `get_scan_traits`, `get_experiment_traits`, and
`list_experiment_trait_sources` need no code change — they already read
`cyl_scan_traits_source.is_latest`; only what computes it changes.

### D4 — `n_plants`: live semi-join, no cache (bloom#656 Fix 1)

```sql
SELECT w.experiment_id, count(DISTINCT p.id)::int AS n_plants
FROM public.cyl_waves  w
JOIN public.cyl_plants p ON p.wave_id = w.id AND p.accession_id IS NOT NULL
JOIN public.cyl_scans  s ON s.plant_id = p.id
WHERE (experiment_id_ IS NULL OR w.experiment_id = experiment_id_)
  AND EXISTS (SELECT 1 FROM public.cyl_scan_traits t WHERE t.scan_id = s.id)
GROUP BY w.experiment_id;
```

247ms for every experiment at once (Benfica's measurement) — well inside a list screen's live budget, and
cheaper to compute than to keep cached. Two incidental, semantics-preserving cleanups from her comment:
`JOIN accessions` → `p.accession_id IS NOT NULL` (the join was only an existence test; the FK guarantees
the referenced row exists, so this preserves the null-accession-plant exclusion the live spec's
`get_experiment_traits` scenario already tests for); dropping the `cyl_experiments` join (only its `id`
was used, already available via `cyl_waves.experiment_id`).

**Why this needs no `is_latest`/`cyl_scan_latest_source` dependency at all:** `EXISTS` asks "does this
scan have *any* trait row," not "does it have a *latest* one" — and because every scan with at least one
`cyl_scan_traits` row has, by the partition-per-`scan_id` rule, exactly one row that is that scan's
latest, "has any row" and "has a latest row" are the same fact for a plain scan. This is why `n_plants`
is correct starting the moment this migration lands, independent of whether D1–D3 have finished
converging for any given scan.

**Re-verified against real data in this pass, not just carried forward from a prior analysis**: a fixture
covering null-accession-plant exclusion, reruns/multiple sources, legacy NULL-source-only scans, and
zero-trait-row scans, plus a comparison against every real experiment in the local dev DB, both against
the current `COUNT(DISTINCT ...)` implementation — see `tests/integration/test_cyl_experiment_summary_counts.py`'s
equivalence tests (tasks.md §2).

### D5 — `n_traits`: cached, refreshed on a schedule, not a per-write trigger (bloom#656 Fix 2)

```sql
CREATE TABLE public.cyl_experiment_trait_counts (
    experiment_id bigint PRIMARY KEY REFERENCES public.cyl_experiments(id) ON DELETE CASCADE,
    n_traits      int NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION public.refresh_cyl_experiment_trait_counts()
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
    DELETE FROM public.cyl_experiment_trait_counts;
    INSERT INTO public.cyl_experiment_trait_counts (experiment_id, n_traits, updated_at)
    SELECT d.experiment_id, count(*), now()
    FROM (
        SELECT DISTINCT w.experiment_id, cst.trait_id
        FROM public.cyl_waves            w
        JOIN public.cyl_plants           p   ON p.wave_id = w.id AND p.accession_id IS NOT NULL
        JOIN public.cyl_scans            s   ON s.plant_id = p.id
        JOIN public.cyl_scan_traits      cst ON cst.scan_id = s.id
        JOIN public.cyl_scan_latest_source l ON l.scan_id = cst.scan_id
            AND cst.source_id IS NOT DISTINCT FROM l.max_source_id
        WHERE cst.trait_id IS NOT NULL
    ) d
    GROUP BY d.experiment_id;
$$;

REVOKE EXECUTE ON FUNCTION public.refresh_cyl_experiment_trait_counts() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.refresh_cyl_experiment_trait_counts() TO service_role;
```

6.6s rebuilds all 9 experiments' rows unconditionally (delete-then-reinsert, not per-row `UPDATE`, so an
experiment that drops to zero matching traits disappears from the cache, matching the "absent if zero"
contract). **Deliberately not scoped to `EXECUTE ... TO bloom_agent, bloom_user, bloom_admin,
authenticated`** the way read RPCs are — this is a maintenance job, not a user-facing call; the only
identity expected to invoke it is whatever runs the schedule (D8), so granting it more broadly would
let any authenticated caller trigger a repeated 6.6s full rebuild for no benefit to them.

**Why the join to `cyl_scan_latest_source` instead of filtering `cst.is_latest`:** `is_latest` is no
longer a stored per-row column (D1) — it's a derived comparison. This refresh query does the same
comparison directly against `cyl_scan_latest_source`, which is exactly what the view does (D3); this
query does not read `cyl_scan_traits_source` at all so its cost isn't affected by anything the view adds
(`source_name`, `pipeline_run_id` lookups it doesn't need).

**The staleness window is explicit, not incidental:** between refreshes, `n_traits` reflects the last
scheduled run, not the current instant. This is an accepted UI-lag tradeoff (Goals/Non-Goals) — the
underlying trait data is correct and immediately consistent; only this one cached count can lag by up to
one refresh interval. `updated_at` is exposed in the table (not yet in the RPC's return shape — see Open
Questions) so this can be surfaced later if staleness ever needs to be visible to a caller.

### D6 — `get_experiment_summary_counts`: live semi-join + cached `n_traits` when unpinned, live helper when pinned

```sql
CREATE OR REPLACE FUNCTION public.compute_cyl_experiment_summary_counts_live(
    experiment_id_ bigint,
    source_id_     bigint,
    run_id_        text
) RETURNS TABLE (
    experiment_id bigint, n_plants int, n_traits int
) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RETURN QUERY
    WITH matched AS (
        SELECT w.experiment_id, p.id AS plant_id, src.trait_name
        FROM public.cyl_waves       w
        JOIN public.cyl_plants      p   ON p.wave_id = w.id AND p.accession_id IS NOT NULL
        JOIN public.cyl_scans       s   ON s.plant_id = p.id
        JOIN public.cyl_scan_traits_source src ON src.scan_id = s.id
        WHERE (experiment_id_ IS NULL OR w.experiment_id = experiment_id_)
          AND (
                (source_id_ IS NOT NULL AND src.source_id = source_id_)
             OR (run_id_ IS NOT NULL AND src.source_id = (
                    SELECT max(s2.source_id) FROM public.cyl_scan_traits_source s2
                    WHERE s2.scan_id = src.scan_id AND s2.trait_id = src.trait_id
                      AND s2.pipeline_run_id = run_id_))
              )
    ),
    plant_counts AS (
        SELECT experiment_id, count(*) AS n_plants
        FROM (SELECT DISTINCT experiment_id, plant_id FROM matched) d GROUP BY experiment_id
    ),
    trait_counts AS (
        SELECT experiment_id, count(*) AS n_traits
        FROM (SELECT DISTINCT experiment_id, trait_name FROM matched WHERE trait_name IS NOT NULL) d
        GROUP BY experiment_id
    )
    SELECT p.experiment_id, p.n_plants, COALESCE(t.n_traits, 0)
    FROM plant_counts p LEFT JOIN trait_counts t ON t.experiment_id = p.experiment_id;
END; $$;

CREATE OR REPLACE FUNCTION public.get_experiment_summary_counts(
    experiment_id_ bigint DEFAULT NULL,
    source_id_     bigint DEFAULT NULL,
    run_id_        text   DEFAULT NULL
) RETURNS TABLE (
    experiment_id bigint, n_plants int, n_traits int
) LANGUAGE plpgsql STABLE SECURITY INVOKER AS $$
BEGIN
    IF source_id_ IS NOT NULL AND run_id_ IS NOT NULL THEN
        RAISE EXCEPTION 'get_experiment_summary_counts: specify at most one of source_id_ and run_id_';
    END IF;

    IF source_id_ IS NULL AND run_id_ IS NULL THEN
        RETURN QUERY
        SELECT p.experiment_id, p.n_plants, COALESCE(c.n_traits, 0)::int
        FROM (
            SELECT w.experiment_id, count(DISTINCT p.id)::int AS n_plants
            FROM public.cyl_waves  w
            JOIN public.cyl_plants p ON p.wave_id = w.id AND p.accession_id IS NOT NULL
            JOIN public.cyl_scans  s ON s.plant_id = p.id
            WHERE (experiment_id_ IS NULL OR w.experiment_id = experiment_id_)
              AND EXISTS (SELECT 1 FROM public.cyl_scan_traits t WHERE t.scan_id = s.id)
            GROUP BY w.experiment_id
        ) p
        LEFT JOIN public.cyl_experiment_trait_counts c ON c.experiment_id = p.experiment_id;
        RETURN;
    END IF;

    -- source_id_/run_id_ pin: neither the live semi-join nor the n_traits cache covers an
    -- arbitrary historical pin — delegate to the shared live helper.
    RETURN QUERY
    SELECT * FROM public.compute_cyl_experiment_summary_counts_live(experiment_id_, source_id_, run_id_);
END; $$;
```

Unlike PR #654's D7, `compute_cyl_experiment_summary_counts_live` here only ever serves the
**source/run-pinned** branch — the unpinned "current latest" case is answered directly by the live
semi-join + cache, so this helper doesn't need an `is_latest`/unpinned disjunct at all, and doesn't
depend on `cyl_scan_latest_source`.

**Same `COUNT(DISTINCT ...)` → `GROUP BY` subquery rewrite as PR #654's D7, kept for the pinned
branches**, for the same reason (avoids a per-experiment `Sort` feeding the aggregate) — this is a
semantics-preserving cleanup independent of everything else in this design, so there's no reason to
regress it just because the unpinned path moved elsewhere.

### D7 — Pinned (`source_id_`/`run_id_`) branches: reasoned, not benchmarked

bloom#656 explicitly flags this as unaddressed — Benfica's measurements all isolate the *default/unpinned*
path. This session's own analysis, offered for confirmation rather than asserted as settled: the pinned
branches use direct equality (`src.source_id = source_id_`) or a subquery already scoped to one
`(scan_id, trait_id)` pair (the `run_id_` branch) — neither depends on a table-wide `is_latest`
computation the way the unpinned path did, so D1–D3's fix doesn't change their cost profile, and D4's
`EXISTS` rewrite doesn't apply to them (a pin is an exact match, not an existence check). **This reasoning
has not been checked against `EXPLAIN (ANALYZE, BUFFERS)` on staging at `experiment_id=1` scale** — no
caller pins `source_id_`/`run_id_` today, so there's been no operational pressure to benchmark this path,
and this sandboxed environment can't run that benchmark. Carried forward as an explicit open item
(Open Questions), not resolved here.

## Risks / Trade-offs

- **The single-transaction backfill's write-blocking window (D3) is new operational surface with no
  precedent in this repo's migrations** — every prior migration either doesn't touch table data at scale
  or (PR #654's design) explicitly avoided holding one transaction open across a large data change. A
  ~2.5s window where write-back RPC calls block is a real, if brief, effect on production traffic during
  deploy. Sized against Benfica's own prod measurement (2,446ms), not a guess — but that number could grow
  as `cyl_scan_traits` grows, and this design doesn't re-derive a growth-rate estimate.
- **The advisory-lock correction (D2) is the second time this exact race has been found by testing rather
  than review** — first in PR #654 (for a boolean column), now again here (for a one-row-per-scan table).
  Both times the "obvious" simpler design (a bare `UPDATE` in PR #654; a bare `ON CONFLICT DO UPDATE` with
  no lock, per Benfica's own comment, here) turned out to have a real concurrency gap under the same class
  of two-writer race. Worth remembering next time a similar upsert-based trigger is proposed in this repo:
  verify the concurrent-writer case directly rather than reasoning about `ON CONFLICT`'s guarantees from
  memory.
- **`n_traits`'s staleness window (D5) is a real, user-visible behavior change** from PR #654's per-write
  trigger design (which was always current, just expensive) — `list_experiments()` can now show a
  slightly-stale trait count for up to one refresh interval after new data lands. Documented as accepted
  (Goals/Non-Goals), but flagged here as a genuine behavior change a reviewer should weigh, not a free
  optimization.
- **D8's refresh-scheduling mechanism is unresolved** — this design's correctness doesn't depend on which
  host runs it, but `n_traits` reads stale data indefinitely (not just for one interval) until something
  is actually scheduled to call `refresh_cyl_experiment_trait_counts()`.

## Migration Plan

**Single migration set, one PR — the direct consequence of D3.** No operator runbook, no phased cutover,
no deploy-policy exception to negotiate.

- **M1** — `cyl_scan_latest_source` table (D1) + trigger function/trigger (D2) + `LOCK TABLE ... IN SHARE
  MODE` + inline backfill (D3) + `cyl_scan_traits_source` view cutover (D3), in that order, in one
  transaction.
- **M2** — `cyl_experiment_trait_counts` table (D5) + `refresh_cyl_experiment_trait_counts()` function,
  plus a one-time initial `SELECT public.refresh_cyl_experiment_trait_counts();` call in the same
  migration (so the cache isn't empty until the first scheduled run fires — see D8).
- **M3** — `compute_cyl_experiment_summary_counts_live` helper (D6, pinned-branch only) +
  `get_experiment_summary_counts` rewrite (D6).

**Rollback ordering**: M3 before M1 if ever rolled back together (a `PL/pgSQL` function body referencing
`cyl_experiment_trait_counts`/`cyl_scan_latest_source` is opaque to Postgres's dependency tracker, unlike
M1's view, which `pg_depend` protects automatically — dropping `cyl_scan_latest_source` while the view
still reads it fails loudly; dropping tables M2/M3 depend on does not, so rollback scripts must respect
this order explicitly, not rely on the catalog to enforce it).

## Open Questions

- **D8 — refresh-scheduling host, genuinely unresolved.** `pg_cron` isn't installed in this stack. Two
  candidates named so far: the `workflows` service (already running, would need new application code to
  poll on an interval) or a scheduled GitHub Action (`on: schedule`, calling the refresh function's
  PostgREST RPC endpoint with the `service_role` key — this repo already has scheduled Actions, e.g. the
  CVE-scan workflows on every PR, so the pattern is precedented, and it needs zero new application code).
  This design proposes the **scheduled GitHub Action** as the default, flagged here for confirmation
  rather than assumed — `tasks.md` carries the task either way (writing the workflow YAML if this is
  confirmed; filing a follow-up issue against `workflows` if not).
- **D7 — pinned-branch cost, not benchmarked.** See D7's own reasoning; needs a real `EXPLAIN (ANALYZE,
  BUFFERS)` against staging once this lands, not resolved from this sandboxed pass.
- **`n_traits`'s `updated_at` isn't surfaced in `get_experiment_summary_counts`'s return shape.** Whether
  `list_experiments()` should ever show "counts as of X" is a product question out of scope here — the
  column exists in `cyl_experiment_trait_counts` so it's available if that's wanted later, but nothing
  reads it today.
- **Whether `cyl_scan_latest_source` and `cyl_experiment_trait_counts` need entries in the five tracked
  `database.types.ts` copies** — resolve the same way PR #654's tasks.md 0.4 did: run `supabase gen types`
  against a local DB with this change's migrations applied and diff against the tracked copies, don't
  assume either way.
