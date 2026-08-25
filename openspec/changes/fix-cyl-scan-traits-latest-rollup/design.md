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
scale, and its trigger writes to a _different_ table, so it never re-fires itself (no recursion guard
needed) and its one-time backfill is a single `INSERT ... SELECT ... GROUP BY` measured at **2,446ms on
prod, cold cache** — cheap enough to run inside the migration itself, not as a batched, resumable,
operator-run procedure gated behind a deploy-policy exception (PR #654's D8).

**[bloom#656](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/656)**, filed against
PR #654 in parallel: the query _shape_ is a second, independent bottleneck. `n_plants` via
`COUNT(DISTINCT ...)` costs 16,520ms for one experiment — 12,927ms of that is dragging 13.8M matched rows
through the join before deduplicating, not the join itself (60ms). Rewritten as a semi-join (`EXISTS`),
all experiments at once cost 247ms — **no cache needed at all**. `n_traits` genuinely needs a full scan
(6,623ms) and does need caching, but PR #654's per-row `AFTER` trigger is the wrong refresh shape: one
write-back upload inserts ~751 trait rows in a loop, so a per-row trigger fires ~751 full-experiment
recomputes for one upload. A dispatched-on-demand refresh (see D8 -- no automatic schedule as of this
design) costs the same 6.6s regardless of ingest volume, and runs where nothing is waiting on it.

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
  `n_plants` is correct and live for every call (no staleness). `n_traits` is cheap to read, with its
  staleness bounded by how often `refresh_cyl_experiment_trait_counts()` is actually called — **by
  design, that's on-demand (`workflow_dispatch`) for staging, and an automatic daily schedule for
  production (bloom#708, this section).** Round 7 found an automatic `schedule:` trigger can't fire
  until a separate promotion PR lands the workflow file on the repo's default branch, and round 8 found
  it would only ever refresh staging's cache regardless — a second, environment-targeting gap. Rather
  than chase promotion to fix a schedule staging doesn't currently need, `on: schedule` was dropped for
  staging (see D5/D8) and an `environment` input closes the production-targeting gap with no new
  secrets. **Correction (bloom#708 investigation): dropping `schedule:` did NOT close the promotion
  gap — `workflow_dispatch` is gated on default-branch presence exactly like `schedule:` is; see D8's
  addendum below.** Production now has its own automatic cadence (D8 addendum) rather than remaining
  on-demand-only indefinitely. That is a UI-lag tradeoff, not a data-integrity one — the underlying
  trait data itself is never inconsistent, only this one summary count can lag, and only until someone
  dispatches or a schedule runs a refresh.
- **Non-Goals:** re-deriving or changing `is_latest`'s selection semantics (per-`scan_id` partition grain,
  `IS NOT DISTINCT FROM` legacy-NULL handling — unchanged from the live `cyl-trait-read` spec). No new MCP
  tool, no `source_id_`/`run_id_` parameter threaded through any analysis tool. No change to
  `get_experiment_traits`, `get_scan_traits`, or `list_experiment_trait_sources`'s own signatures — they
  read `cyl_scan_traits_source.is_latest` exactly as before; only what's underneath that column changes.
  No RLS change on any _existing_ table. Real-time (sub-refresh-interval) freshness for `n_traits` —
  that tradeoff is deliberate (see D5), not an oversight. (The two _new_ tables this change adds do get
  RLS enabled, matching this repo's own convention for every other `cyl_*` table — see D2a/D5a, added
  after this proposal's own review found they'd shipped without it.)

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
28.8M existing rows without holding one transaction open for the duration. A table scoped to _scans_
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
    v_new_scan bigint := NEW.scan_id;  -- NULL on DELETE
    v_old_scan bigint := OLD.scan_id;  -- NULL on INSERT
    v_lo       bigint;
    v_hi       bigint;
BEGIN
    -- A scan-reassigning UPDATE (OLD.scan_id <> NEW.scan_id) affects TWO scans -- see D2b for why
    -- this needs its own branch, added post-review. Lock order is sorted (lower id first), not
    -- NEW-then-OLD, so two concurrent cross-scan reassignments moving rows in opposite directions
    -- can't deadlock each other.
    IF v_old_scan IS NOT NULL AND v_old_scan IS DISTINCT FROM v_new_scan THEN
        v_lo := least(v_new_scan, v_old_scan);
        v_hi := greatest(v_new_scan, v_old_scan);
        PERFORM pg_advisory_xact_lock(v_lo);
        PERFORM pg_advisory_xact_lock(v_hi);
    ELSIF v_new_scan IS NOT NULL THEN
        -- Serializes concurrent writers to the SAME scan_id — see the Context section's empirical
        -- finding.
        PERFORM pg_advisory_xact_lock(v_new_scan);
    ELSE
        PERFORM pg_advisory_xact_lock(v_old_scan);
    END IF;

    IF v_new_scan IS NOT NULL THEN
        INSERT INTO public.cyl_scan_latest_source (scan_id, max_source_id)
        SELECT v_new_scan, max(source_id) FROM public.cyl_scan_traits WHERE scan_id = v_new_scan
        ON CONFLICT (scan_id) DO UPDATE SET max_source_id = EXCLUDED.max_source_id;
    END IF;

    IF v_old_scan IS NOT NULL AND v_old_scan IS DISTINCT FROM v_new_scan THEN
        INSERT INTO public.cyl_scan_latest_source (scan_id, max_source_id)
        SELECT v_old_scan, max(source_id) FROM public.cyl_scan_traits WHERE scan_id = v_old_scan
        ON CONFLICT (scan_id) DO UPDATE SET max_source_id = EXCLUDED.max_source_id;
    END IF;

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
connection A inserts a trait row for the _same new_ `scan_id` (`source_id=5`), then attempts its own
upsert, which blocks on B's uncommitted row; B commits; A's blocked upsert unblocks and applies
`SET max_source_id = EXCLUDED.max_source_id` using the value A computed _before_ the wait (`5`, since A
could not see B's uncommitted row when it computed its own proposed value) — final state `5`, not the
correct `6`. Confirmed this is not fixed by using a fresh correlated subquery in the `SET` clause instead
of `EXCLUDED` either (same wrong result) — Postgres fixes one snapshot for the whole statement at
statement start, before the conflict wait, regardless of where in the statement a subquery sits. Adding
`pg_advisory_xact_lock(affected_scan_id)` _before_ the read-then-upsert and rerunning the identical race
produced the correct result (`6`): the lock forces A's entire read-then-write to happen as a fresh
statement _after_ B's commit, with a fresh snapshot that sees it. This covers both the shape PR #654's
own testing already found (two writers racing a _pre-existing_ scan) and the brand-new-scan variant,
uniformly, since the lock serializes on `scan_id` regardless of whether a row already exists.

**`SECURITY DEFINER` necessity — same reasoning as PR #654's D2, unchanged:** every role that can write
`cyl_scan_traits` today (`postgres` via the write-back RPC, `bloom_admin`, `bloom_writer`) already has
privilege equal to or exceeding what this trigger's own maintenance write needs as `SECURITY INVOKER`, so
`SECURITY DEFINER` is not functionally required by any writer that exists today — kept defensively, in
case a future narrowly-scoped writer role would otherwise be blocked from maintaining
`cyl_scan_latest_source` by its own RLS.

**Per-row granularity matches how writes already happen**, same reasoning as PR #654's D2:
`insert_cyl_result_envelope` inserts one row per trait per `INSERT` in a loop, so a per-row trigger fires
once per trait — but unlike PR #654's design, each firing here only ever touches _this one scan's_ small
table row (an `O(1)` upsert, not a table-wide recompute), so firing 751 times for one envelope costs 751
cheap upserts, not 751 full-experiment aggregations. This is the concrete sense in which this design
avoids bloom#656's per-row-trigger objection without needing a scheduled refresh for `is_latest` itself
— only `n_traits` (D5) needs one.

**Deadlock note, reachable TODAY via the break-glass path, not just a future automated writer.**
`pg_advisory_xact_lock` participates in Postgres's deadlock detector. A single transaction that ever
touches multiple `scan_id`s across _separate_ trigger firings — e.g. a future multi-scan batch writer
inserting for several scans in one transaction — could deadlock against another transaction acquiring the
same set in the opposite order. `insert_cyl_result_envelope`, the sole _automated_ writer, is
single-scan-per-call, so this never arises through that path. This is a different, broader risk than D2b's
cross-scan `UPDATE` case (which the sorted-lock-order fix below closes for a _single_ trigger firing
needing both locks at once) — a future multi-scan batch writer would still need to acquire `scan_id` locks
in a consistent order across its own separate statements to stay safe.

**Found in a fourth review round: this is not purely hypothetical.** `bloom_admin`'s break-glass path
already supports reassigning multiple, independent rows across two _different_ scan pairs within one
transaction — the exact shape `test_multi_row_cross_scan_update_recomputes_both_scans` exercises for one
pair extends trivially to two: an operator doing a batch of independent corrections in one sitting could
naturally produce two transactions each touching two disjoint scan-id pairs in the opposite order (txn 1:
`scan_5→scan_10` then `scan_3→scan_7`; txn 2: `scan_3→scan_7` then `scan_5→scan_10`), a genuine circular
wait. Sorted acquisition only orders the two locks _within a single firing_ (D2b); it does not impose a
transaction-wide order across separate firings for different pairs. **Accepted, not fixed**: Postgres's
deadlock detector aborts one of the two transactions with a clear `DeadlockDetected` error rather than
corrupting data, and the operator simply retries the failed correction — a transaction-wide multi-pair
lock-ordering scheme would close this but adds real complexity for a failure mode that already fails safe
and is self-healing via retry. No test currently exercises this two-pair shape; adding one is a reasonable
follow-up but not a merge blocker given the failure mode is safe.

### D2b — Cross-scan `UPDATE`: both scans must be recomputed, not just `NEW.scan_id` (found in a second

review round, not the first)

**A second `/review-pr` round caught what the first round's fixes missed:** the trigger's original
`affected_scan_id := COALESCE(NEW.scan_id, OLD.scan_id)` only ever resolves to `NEW.scan_id` for an
`UPDATE` (never `NULL`), so a row whose `scan_id` itself changes — e.g. `bloom_admin`'s break-glass access
correcting a mis-attributed trait row — only ever got the _new_ scan recomputed. The _old_ scan's
`cyl_scan_latest_source` row silently kept whatever `max_source_id` it had before the reassignment. If the
reassigned row held that old scan's current max, every remaining row for that scan would evaluate
`is_latest = false` — the scan effectively vanishes from `get_scan_traits`/`get_experiment_traits`/
`n_traits` — until some unrelated future write happens to touch that scan again. This is a genuine
data-integrity bug, not staleness: nothing about it self-corrects on a schedule the way D5's `n_traits`
cache does.

Reproduced directly: seeded two scans, delivered two sources to scan A (older `old_source`, newer
`new_source`), delivered scan B's own source, then `UPDATE cyl_scan_traits SET scan_id = B WHERE scan_id =
A AND source_id = new_source` (reassigning A's newest row to B). Against the original trigger, scan A's
`max_source_id` stayed stuck at the now-departed `new_source` instead of falling back to `old_source`
(`test_cross_scan_update_recomputes_both_scans` fails with exactly this value against the unpatched
function — confirmed by temporarily redeploying it). The fix (D2's code block above) recomputes both
`NEW.scan_id` and `OLD.scan_id` whenever they differ, with sorted lock acquisition (lower scan_id first)
so two concurrent cross-scan reassignments moving rows in opposite directions (`A→B` and `B→A`
simultaneously) can't deadlock each other by each locking their own "new" scan first.

Not caught by the first review round's fixes, nor by any test written before this round — the existing
concurrency/boundary tests only ever exercised same-scan `UPDATE`s (correcting a value or `source_id`) and
straightforward insert/delete, never a `scan_id`-changing one.

### D2a — RLS: enabled on both new tables, matching every other `cyl_*` table (found in review, not in

the original pass)

**This proposal's own review caught a real gap**: `cyl_scan_latest_source` (this section) and
`cyl_experiment_trait_counts` (D5) originally shipped with a `GRANT SELECT` to the four read roles but no
`ALTER TABLE ... ENABLE ROW LEVEL SECURITY` — every other `cyl_*` table in this repo has RLS enabled, even
where the policy itself is permissive (`USING (true)`). This isn't a cosmetic inconsistency: Supabase's
default privileges grant **`anon` a raw `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` grant on every new
public-schema table**, independent of any policy. Verified directly against a local Postgres — before RLS
was added, `SET LOCAL ROLE anon; INSERT INTO cyl_scan_latest_source ...` **succeeded**: an unauthenticated
caller could directly corrupt `is_latest` for any scan. With RLS enabled and only `bloom_admin` (`FOR
ALL`), `bloom_agent`/`bloom_user`/`authenticated` (`FOR SELECT`) policies defined — matching
`cyl_scan_traits`'s own exact policy set — the same `INSERT` now fails with `new row violates row-level
security policy`, and a plain `anon` `SELECT` succeeds but returns zero rows (silently filtered, not
errored) rather than exposing real data. Both behaviors are asserted directly, not inferred, in
`test_cyl_scan_latest_source.py`/`test_cyl_experiment_trait_counts.py`'s RLS test sections.

### D3 — Single migration: schema, trigger, inline backfill, and view cutover together

**No phased Phase 1/Phase 2 split, unlike PR #654.** That split existed because PR #654's backfill was a
batched, potentially long-running, operator-invoked procedure — cutting the view over before it finished
would have meant readers seeing `is_latest = false` (the column's default) for any not-yet-backfilled row.
This design's backfill is a single aggregate query over `cyl_scan_traits`, measured at 2,446ms on prod —
short enough to run inside the same migration transaction as the schema and the view cutover, with no
separate operator step and no runbook.

**The one new subtlety a single-transaction backfill introduces, and how it's closed:** between the
moment `CREATE TRIGGER` takes effect and the moment the backfill's `SELECT` executes, a _concurrent_
write transaction that began before this migration's DDL is visible to it (i.e., before this migration
commits) would see neither the new trigger (its own snapshot predates the DDL) nor get captured by the
backfill (if it commits after the backfill's `SELECT` already ran) — a scan could fall into a gap where
nothing populates its `cyl_scan_latest_source` row. This is closed by a table-level lock that blocks
concurrent writers for the ~2.5s backfill — and, verified against `pg_locks` after an initial draft of
this section incorrectly described the lock mode (see below), concurrent _readers_ are unaffected either
way:

```sql
BEGIN;

-- 1. Schema (D1) + trigger (D2). CREATE TRIGGER takes a ShareRowExclusiveLock on cyl_scan_traits —
--    NOT AccessExclusiveLock, an error in an earlier draft of this section caught during review and
--    corrected after confirming the actual mode against pg_locks directly. This lock is held for the
--    REST of the transaction (locks aren't released until COMMIT, not "released after the statement"
--    as that earlier draft also assumed) — which is exactly what makes step 2 below redundant, not
--    the mechanism protecting the backfill.
CREATE TABLE public.cyl_scan_latest_source ( ... );
CREATE FUNCTION public.maintain_cyl_scan_latest_source() ...;
CREATE TRIGGER maintain_cyl_scan_latest_source_after_write ...;

-- 2. Redundant with step 1's still-held ShareRowExclusiveLock (confirmed empirically, not just
--    reasoned) — kept as an explicit, self-documenting assertion of the safety property this
--    migration relies on, not as the actual mechanism. Concurrent WRITERS are blocked (SHARE MODE,
--    like ShareRowExclusiveLock, conflicts with ROW EXCLUSIVE, which INSERT/UPDATE/DELETE need) for
--    the remainder of this transaction — any write-back RPC call that lands during the backfill
--    below simply waits the ~2.5s for this transaction to commit, then proceeds normally, now seeing
--    the trigger created in step 1 and computing correctly against a snapshot that includes the
--    backfill's own committed data. Concurrent READERS are unaffected (SHARE MODE doesn't conflict
--    with AccessShareLock, which plain SELECT needs) — verified directly against `pg_locks` and a
--    concurrent `SELECT`'s actual wall-clock time (0.01s, not blocked) during design review.
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
scan have _any_ trait row," not "does it have a _latest_ one" — and because every scan with at least one
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
LANGUAGE plpgsql  -- not plain SQL: PERFORM (D5b's advisory lock) needs PL/pgSQL
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    -- D5b: serializes concurrent refreshes -- reproduced empirically (see D5b) that two
    -- overlapping calls raise "duplicate key value violates unique constraint
    -- cyl_experiment_trait_counts_pkey" without this. Two-int form, not single-bigint -- see D5c
    -- for why sharing D2's per-scan keyspace is a real (if narrow) risk, confirmed disjoint after.
    PERFORM pg_advisory_xact_lock(0, hashtext('refresh_cyl_experiment_trait_counts'));

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
END;
$$;

-- Supabase auto-grants EXECUTE on new public-schema functions to anon/authenticated/service_role,
-- so REVOKE ... FROM PUBLIC alone wouldn't close that (see D6's anon-EXECUTE finding for why this
-- matters more than it sounds).
REVOKE EXECUTE ON FUNCTION public.refresh_cyl_experiment_trait_counts() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.refresh_cyl_experiment_trait_counts() TO service_role;
```

6.6s rebuilds all 9 experiments' rows unconditionally (delete-then-reinsert, not per-row `UPDATE`, so an
experiment that drops to zero matching traits disappears from the cache, matching the "absent if zero"
contract). **Deliberately not scoped to `EXECUTE ... TO bloom_agent, bloom_user, bloom_admin,
authenticated`** the way read RPCs are — this is a maintenance job, not a user-facing call; the only
identity expected to invoke it is whoever/whatever dispatches a refresh (D8 -- currently manual, no
automatic caller), so granting it more broadly would let any authenticated caller trigger a repeated
6.6s full rebuild for no benefit to them.

**Why the join to `cyl_scan_latest_source` instead of filtering `cst.is_latest`:** `is_latest` is no
longer a stored per-row column (D1) — it's a derived comparison. This refresh query does the same
comparison directly against `cyl_scan_latest_source`, which is exactly what the view does (D3); this
query does not read `cyl_scan_traits_source` at all so its cost isn't affected by anything the view adds
(`source_name`, `pipeline_run_id` lookups it doesn't need).

**The staleness window is explicit, not incidental:** between refreshes, `n_traits` reflects the last
scheduled run, not the current instant. This is an accepted UI-lag tradeoff (Goals/Non-Goals) — the
underlying trait data is correct and immediately consistent; only this one cached count can lag by up to
one refresh interval. `updated_at` is exposed in the table and, as of round 6, in
`get_experiment_summary_counts`'s own return shape (`n_traits_updated_at`) and in
`list_available_experiments`'s printed output (`bloommcp/src/bloom_mcp/sections/core/list_available_experiments.py`)
-- see the round-6 note directly below for why this was closed rather than deferred a third time.

**"Bounded to one refresh interval" assumes the schedule is actually running — found in a fourth review
round to currently NOT be true, and round 7 found the gap is bigger than "not yet confirmed."** The
`STAGING_API_URL` secret this paragraph originally flagged as unprovisioned turned out to be unnecessary
entirely: the value is the same public, stable hostname already committed as `API_EXTERNAL_URL` in
`.env.staging.defaults`, so it's hardcoded as a literal in the workflow instead of a secret (tasks.md 5.2).
The one remaining credential, `STAGING_SERVICE_ROLE_KEY`, already existed before this change
(`deploy.yml`'s own use) — so no secret provisioning blocks this schedule anymore. But round 7 found a
second, larger gap the secret framing had been masking: this PR's base branch is `staging`, and GitHub
Actions `schedule:` triggers only ever fire from the copy of the workflow file on the repository's
**default branch** (`main` — GitHub Actions' own documented behavior, and this repo's
`promote-security-to-main.yml` states the identical fact for its own scheduled bot). So merging this PR
lands the workflow on `staging` only; the daily cron stays inert until a later promotion PR carries it to
`main`. `workflow_dispatch` (tasks.md 5.3) can confirm the call logic works when run against `staging`,
but proves nothing about whether the automatic daily schedule is live — those are two different facts.
Until the workflow is actually promoted to `main`, `n_traits` is frozen at whatever the migration's
one-time `SELECT public.refresh_cyl_experiment_trait_counts();` computed at deploy time -- staleness is
currently **unbounded**, not "up to a day." Tracked as a pre-close gate on bloom#637 alongside tasks.md
5.3 and 5.4 (promotion to `main` confirmed), not merely a nice-to-have.

**Round 8: promotion to `main` is not itself sufficient — the workflow only ever targets staging, on
production too.** `.github/workflows/refresh-cyl-experiment-trait-counts.yml`'s `STAGING_API_URL` is a
hardcoded literal pointing at `staging.bloom.salk.edu` (D8, above). Production is a genuinely separate
host — `.env.prod.defaults` sets `API_EXTERNAL_URL=https://bloom.salk.edu/api`, a different Supabase
instance — and `deploy.yml`'s `deploy-production` job applies this PR's migrations there too on push to
`main`, including the one-time inline `refresh_cyl_experiment_trait_counts()` call. So even after
promotion, production's cache gets populated exactly once (at deploy time) and **never refreshed again**
by any existing mechanism, regardless of the branch-promotion status above — a stronger, permanent-not-
just-unbounded-until-promoted claim, and specific to production, which is where the live `bloommcp-prod`
MCP endpoint (`bloommcp/docs/connecting-claude-code.md`) actually serves real researchers. Fixing this
requires either a second, production-targeted workflow or parameterizing the existing one by environment
— a real design decision (which secret, which trigger, whether it's a matrix job) this review round
surfaces but does not resolve; tracked as tasks.md 5.5, a new gate distinct from 5.4's branch-promotion
check.

Correction to the earlier promotion-timeline citation: `promote-security-to-main.yml`'s bot is scoped
strictly to security/CVE-pattern commits (`scripts/promote_security_to_main.sh`'s `_subject_is_security`/
`_commit_is_pure_cve_surface` checks) and would never pick up this PR's own commits or a new non-security
workflow file. The actual, faster path is this repo's separate, manual `staging -> main` promotion
practice — periodic "chore: promote staging to main" PRs, most recently authored by @blm3886 (this PR's
own approver) roughly every 1-2 weeks (e.g. #667, #627, #607). Tasks.md 5.4 should be read as "ask Benfica
to fold this workflow file into her next promotion PR," not as a passive multi-week wait.

**Round 6: the caller-facing visibility gap this same paragraph flagged in round 4 is now closed, not
deferred a third time.** An external PR-comment review re-raised exactly this point -- `n_traits`'s
staleness was still invisible where a scientist actually sees it, two rounds after it was first
identified. Given the gap above means staleness is currently _unbounded_, not a bounded
UI-lag nicety, leaving it invisible a third time was judged no longer a neutral deferral. Closed by
adding `n_traits_updated_at` to `get_experiment_summary_counts`'s `RETURNS TABLE` (`NULL` for a pinned
call, which has no cache to be stale against; the cache row's own `updated_at`, or `NULL` if never
populated, for an unpinned call) and threading it through `ExperimentSummary.trait_columns_updated_at` to
`list_available_experiments`'s printed `Traits: {n} (as of {ts})` / `(never refreshed)` output. Changing
the RPC's `RETURNS TABLE` shape required `DROP FUNCTION` before `CREATE FUNCTION` in both the forward
migration and its rollback (Postgres refuses `CREATE OR REPLACE FUNCTION` across a return-type change) --
this also surfaced (and fixed) a latent gap in this migration's own idempotency tests, which had never
exercised a shape-changing re-application before.

### D5b — Concurrent refreshes: unguarded DELETE+INSERT raced, confirmed exploitable and fixed (found in

a second review round)

**The first pass of this function had no concurrency control at all** — a bare `DELETE FROM
cyl_experiment_trait_counts; INSERT INTO ... SELECT ... GROUP BY`, no lock, no `ON CONFLICT`. A second
`/review-pr` round asked what happens under two overlapping calls (e.g. a manual `workflow_dispatch`
overlapping the cron schedule, or any future second caller of this function); reproduced directly against
a local Postgres rather than reasoned about: two connections each call the function, the first holds its
transaction open, the second's call runs concurrently — the second's `DELETE` finds nothing to delete
(the first's rows are new tuples outside its snapshot, since they don't exist until the first commits),
so its `INSERT` collides on the `experiment_id` primary key with the first's already-committed rows:
`duplicate key value violates unique constraint "cyl_experiment_trait_counts_pkey"`. Confirmed reproduced
before the fix, confirmed resolved after adding `pg_advisory_xact_lock(hashtext('refresh_cyl_experiment_trait_counts'))`
as the function's first statement (D5's code block above) — re-running the identical two-connection race
with the lock in place produced no error.

**A fixed lock key is correct here, unlike D2's per-scan `pg_advisory_xact_lock(scan_id)`**, because this
function always rebuilds the _whole_ table in one pass — there is no finer-grained key (no single
`experiment_id`) to scope the lock to; every call needs to exclude every other call, full stop.

### D5c — Advisory-lock keyspace collision: the refresh function's lock shared D2's keyspace (found in

a third review round)

**A third `/review-pr` round questioned an assumption the second round's own fix (D5b) never checked**:
`pg_advisory_xact_lock(hashtext('refresh_cyl_experiment_trait_counts'))` (single-bigint form) and D2's
per-scan `pg_advisory_xact_lock(scan_id)` (also single-bigint form) share **the same global advisory-lock
keyspace** — Postgres's single-argument `pg_advisory_xact_lock(key bigint)` has one flat namespace per
database, with no partitioning by caller or purpose. `hashtext(...)` can return any 4-byte signed int, so
in principle it could collide with a real `scan_id`, spuriously serializing this function's whole-table
refresh against one unrelated scan's write (and vice versa) — a correctness-neutral but real availability
risk (an unlucky refresh could block on, or block, an unrelated write for no reason tied to actual data
contention).

Not just reasoned about: found the literal colliding `scan_id` for this function's fixed key
(`hashtext('refresh_cyl_experiment_trait_counts')` evaluates to `-124364726`) and confirmed empirically,
via two real connections, that a `pg_advisory_xact_lock(-124364726)` (single-bigint, simulating a scan with
that id) and the refresh function's own lock call **did** contend for the same key before the fix — and,
after switching to the two-int form `pg_advisory_xact_lock(0, hashtext(...))`, confirmed the opposite: a
second connection could still acquire `pg_advisory_xact_lock(-124364726)` (single-bigint) immediately while
the two-int lock was held elsewhere, and vice versa. Postgres's two-argument form is a **genuinely disjoint
keyspace** from the single-argument form (visible in `pg_locks` as `objsubid = 2` vs `objsubid = 1` for the
same numeric key) — not merely a different-looking call that happens to avoid this particular collision by
luck. Fixed by changing D5's lock call to `pg_advisory_xact_lock(0, hashtext(...))`.

### D5a — RLS on `cyl_experiment_trait_counts`

Same finding and fix as D2a: `ENABLE ROW LEVEL SECURITY` + the same four-role policy set as
`cyl_scan_traits` (`bloom_admin` `FOR ALL`; `bloom_agent`/`bloom_user`/`authenticated` `FOR SELECT`, all
`USING (true)`). Without it, Supabase's default `anon` grant would let an unauthenticated caller `INSERT`
fabricated `n_traits` values directly into this table — confirmed exploitable before the fix, confirmed
blocked after, the same way as D2a.

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

```sql
-- REVOKE also from anon, not just PUBLIC, on both functions — see the finding below.
REVOKE EXECUTE ON FUNCTION public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text)
    FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

REVOKE EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text)
    FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;
```

**Anon-EXECUTE finding, caught in review:** an earlier draft of both `REVOKE` statements above read
`FROM PUBLIC` only — the same lesson D5 already states for `refresh_cyl_experiment_trait_counts` (Supabase
auto-grants EXECUTE on new public-schema functions to `anon`/`authenticated`/`service_role`) wasn't applied
to these two. Verified directly: `SET LOCAL ROLE anon; SELECT * FROM
compute_cyl_experiment_summary_counts_live(...)` **succeeded** before the fix. This mattered more here than
a typical over-broad grant would, because `compute_cyl_experiment_summary_counts_live` is `SECURITY
DEFINER` — an `anon` caller invoking it directly runs with the _definer's_ elevated privilege, bypassing
whatever table-level grants `anon` itself lacks, rather than being stopped by RLS/grants the way a
`SECURITY INVOKER` call would be. `get_experiment_summary_counts` itself (`SECURITY INVOKER`) had the same
gap, inherited unchanged from its original bloom#625 definition — fixed here too since this migration
already re-touches that function's grants, not left as a separately-scoped pre-existing issue.
`test_anon_has_no_execute_grant` (parametrized over both functions) asserts this directly via
`has_function_privilege`.

Unlike PR #654's D7, `compute_cyl_experiment_summary_counts_live` here only ever serves the
**source/run-pinned** branch — the unpinned "current latest" case is answered directly by the live
semi-join + cache, so this helper doesn't need an `is_latest`/unpinned disjunct at all, and doesn't
depend on `cyl_scan_latest_source`.

**Same `COUNT(DISTINCT ...)` → `GROUP BY` subquery rewrite as PR #654's D7, kept for the pinned
branches**, for the same reason (avoids a per-experiment `Sort` feeding the aggregate) — this is a
semantics-preserving cleanup independent of everything else in this design, so there's no reason to
regress it just because the unpinned path moved elsewhere.

### D7 — Pinned (`source_id_`/`run_id_`) branches: reasoned, not benchmarked

bloom#656 explicitly flags this as unaddressed — Benfica's measurements all isolate the _default/unpinned_
path. This session's own analysis, offered for confirmation rather than asserted as settled: the pinned
branches use direct equality (`src.source_id = source_id_`) or a subquery already scoped to one
`(scan_id, trait_id)` pair (the `run_id_` branch) — neither depends on a table-wide `is_latest`
computation the way the unpinned path did, so D1–D3's fix doesn't change their cost profile, and D4's
`EXISTS` rewrite doesn't apply to them (a pin is an exact match, not an existence check). **This reasoning
has not been checked against `EXPLAIN (ANALYZE, BUFFERS)` on staging at `experiment_id=1` scale** — no
caller pins `source_id_`/`run_id_` today, so there's been no operational pressure to benchmark this path,
and this sandboxed environment can't run that benchmark. Carried forward as an explicit open item
(Open Questions), not resolved here.

### D9 — RLS does not govern `TRUNCATE`: both new tables retained a raw grant despite RLS (found in a

third review round)

**A third `/review-pr` round questioned whether D2a/D5a's RLS fix was actually complete**, and found it
wasn't: RLS in Postgres governs `SELECT`/`INSERT`/`UPDATE`/`DELETE`, but **not `TRUNCATE`** — this is a
structural Postgres limitation, not a policy gap that a `FOR ALL`/`FOR TRUNCATE` policy could close (no
such policy command type exists). Supabase's default privileges separately grant `anon`/`authenticated` a
raw `TRUNCATE`/`REFERENCES`/`TRIGGER` grant on every new public-schema table, and D2a/D5a's RLS fix — which
closed `INSERT`/`UPDATE`/`DELETE` and `SELECT` — left that grant untouched on both `cyl_scan_latest_source`
and `cyl_experiment_trait_counts`.

Confirmed exploitable, not assumed: `SET LOCAL ROLE anon; TRUNCATE public.cyl_scan_latest_source;`
succeeded before this fix, despite `anon`'s `INSERT` on the same table already being correctly denied by
RLS (D2a) — a stark illustration of why "RLS is enabled and `INSERT` is denied" does not imply "this table
is locked down." The blast radius is worse than a table-local data-loss bug: `cyl_scan_traits_source`
inner-joins `cyl_scan_latest_source` (D3), so truncating it would zero out `is_latest` for **every scan
system-wide**, breaking `get_scan_traits`/`get_experiment_traits` for the whole table, not just this
change's own two new tables.

Fixed with an explicit `REVOKE TRUNCATE, REFERENCES, TRIGGER ... FROM anon, authenticated` on both new
tables, following this repo's own precedent (`20260504000002_grant_all_scope_reduction.sql`, which made the
exact same fix for `bloom_admin` on a different set of tables, but never extended it to `anon`/
`authenticated` on any table). **This is a pre-existing, repo-wide gap this migration does not attempt to
close beyond its own two new tables** — confirmed `anon` can still `TRUNCATE public.cyl_scan_traits` itself
today, unrelated to anything this proposal changes, and out of scope for it; worth a separate, repo-wide
follow-up.

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
  trait count that's stale by an amount bounded only by how often someone dispatches a refresh (no
  automatic schedule for either environment, per D8's round-9 redesign). Documented as accepted
  (Goals/Non-Goals), but flagged here as a genuine behavior change a reviewer should weigh, not a free
  optimization.
- **(Historical, round 1-era note; D8 has since been resolved — see D8 itself for the current, redesigned
  mechanism.) D8's refresh-scheduling mechanism is unresolved** — this design's correctness doesn't depend on which
  host runs it, but `n_traits` reads stale data indefinitely (not just for one interval) until something
  is actually scheduled to call `refresh_cyl_experiment_trait_counts()`.
- **A third instance of a testing-methodology gap, this time in the concurrency tests themselves, not just
  the SQL** — this proposal's own `/review-pr` pass found that the first drafts of
  `test_concurrent_first_insert_to_same_new_scan_converges_to_true_max` and its rerun sibling had a
  construction bug that made them pass whether or not `pg_advisory_xact_lock` existed: `cyl_trait_sources.id`
  is a monotonic identity, and the original tests let the connection that resolves _last_ (after blocking)
  happen to hold the numerically _higher_ id — so its own pre-block value was already the true max,
  regardless of the lock. Fixed by minting both ids upfront and assigning the _lower_ one to the
  last-to-resolve connection, which is the only ordering where a missing lock actually produces a wrong
  result. Confirmed by temporarily removing the lock and watching both tests fail (5008 instead of the true 5009) before restoring it. Same lesson as the two SQL-level findings above, applied one level up: verify a
  test actually discriminates pass/fail states, don't assume a plausible-looking construction does.
- **This proposal's own review found and fixed a real security gap, not a stylistic one**: both new tables
  (`cyl_scan_latest_source`, `cyl_experiment_trait_counts`) shipped without RLS enabled, and two `EXECUTE`
  grants (`compute_cyl_experiment_summary_counts_live`, `get_experiment_summary_counts`) shipped without
  revoking Supabase's default `anon` auto-grant. Both were confirmed exploitable (an unauthenticated `anon`
  role could `INSERT` directly into either new table, and could call the `SECURITY DEFINER` helper directly)
  before the fixes landed, and confirmed closed after, via dedicated tests in both files' RLS/grant sections
  — not caught by CI (RLS gaps and over-broad `EXECUTE` grants don't fail any existing check in this repo).
- **A SECOND `/review-pr` round, run specifically to check whether the first round's fixes actually held up
  under fresh adversarial scrutiny, found three more genuine bugs the first round missed**: the cross-scan
  `UPDATE` trigger gap (D2b), the unguarded concurrent-refresh race (D5b), and the trigger function's own
  missing (if practically inert) `anon` `EXECUTE` revoke — plus a real, if narrower, security hardening item
  (D8's HTTPS validation for `STAGING_API_URL`, added below) and several test-quality issues (a grant-check
  test that omitted the one role it existed to check, a boundary test that couldn't distinguish "row exists
  with NULL" from "row absent," orphaned rows accumulating across the real-connection concurrency tests'
  commits). **The pattern across both rounds is consistent**: every genuinely new finding came from either
  reproducing a claim directly against Postgres rather than reasoning about it, or from re-deriving a test's
  discriminating power by hand rather than trusting that a plausible-looking construction actually tests
  what its name claims. Two independent review passes each found real bugs the other missed — this is not
  evidence either pass was thorough enough to be the last one needed, only that adversarial re-review of
  this kind of concurrency-heavy, security-adjacent change keeps paying off past the first round.
- **A THIRD `/review-pr` round found a real bug in each of the previous two rounds' own fixes**: D2a/D5a's
  RLS fix (round 1) didn't cover `TRUNCATE` (D9) — a structural Postgres limitation the RLS fix could not
  have closed even in principle, confirmed exploitable on both new tables before the fix; D5b's advisory
  lock (round 2) shared a keyspace with D2's per-scan lock (D5c), confirmed via the literal colliding
  `scan_id` value, not just argued from the forms looking different; and the rollback guard's function-only
  existence check (round 1) was a narrower invariant than a table-existence check, closed in the Migration
  Plan section above. **A second instance of the round-2 testing-methodology lesson, this time one level up
  from D2b's own bug**: the first draft of this round's cross-scan-deadlock test used the same "A runs to
  completion, then B starts" construction as the existing sequential test, and it passed unchanged even
  after the trigger's lock order was deliberately reverted to unsorted `NEW`-then-`OLD` — because that
  construction can only ever prove B blocks on a lock A already holds, never a genuine circular wait between
  two in-flight transactions. Rebuilt with a `threading.Barrier` so both connections issue their conflicting
  `UPDATE`s at the same instant; this reliably reproduced a real `DeadlockDetected` error against the
  unsorted trigger (1 in 3 attempts) and zero deadlocks across 15 attempts against the correct one — the
  same lesson as round 2's monotonic-id construction bug, recurring in a different shape: a concurrency
  test's construction has to be checked for whether it can actually distinguish the buggy and fixed
  behavior, not just whether it "looks like" a concurrency test. Three independent review rounds have now
  each found real bugs the prior rounds missed, including bugs in the prior rounds' own remediations —
  strong evidence this class of change keeps rewarding another adversarial pass longer than intuition
  suggests, not evidence that three rounds is enough to stop.

## Migration Plan

**Single migration set, one PR — the direct consequence of D3.** No operator runbook, no phased cutover,
no deploy-policy exception to negotiate.

- **M1** — `cyl_scan_latest_source` table (D1) + trigger function/trigger (D2) + `LOCK TABLE ... IN SHARE
MODE` + inline backfill (D3) + `cyl_scan_traits_source` view cutover (D3), in that order, in one
  transaction.
- **M2** — `cyl_experiment_trait_counts` table (D5) + `refresh_cyl_experiment_trait_counts()` function,
  plus a one-time initial `SELECT public.refresh_cyl_experiment_trait_counts();` call in the same
  migration (so the cache isn't empty before anyone dispatches a refresh — see D8).
- **M3** — `compute_cyl_experiment_summary_counts_live` helper (D6, pinned-branch only) +
  `get_experiment_summary_counts` rewrite (D6).

**Rollback ordering**: the real constraint is **M3 → M2 → M1** (not just "M3 before M1" — an earlier
draft of this note omitted M2, incompletely, which review caught), because a `PL/pgSQL` function body
referencing `cyl_experiment_trait_counts`/`cyl_scan_latest_source` is opaque to Postgres's dependency
tracker, unlike M1's view, which `pg_depend` protects automatically — dropping `cyl_scan_latest_source`
while `refresh_cyl_experiment_trait_counts()` (M2) still reads it, or dropping `cyl_experiment_trait_counts`
while `get_experiment_summary_counts` (M3) still reads it, does not fail loudly at `DROP` time; it fails
later, at the next call, with "relation ... does not exist". Rather than relying on an operator reading
this paragraph mid-incident, each rollback script now enforces its own precondition directly: M1's and
M2's rollback SQL each `RAISE EXCEPTION` if the migration that depends on it hasn't been rolled back yet,
rather than silently proceeding into a corrupted state. `test_rollback_guard_blocks_out_of_order_rollback`
(in both `test_cyl_scan_latest_source.py` and `test_cyl_experiment_trait_counts.py`) confirms each guard
actually fires. Both guards' catalog queries are anchored with `AND pronamespace = 'public'::regnamespace`
(added in the second review round) so they can't be fooled by a same-named function in a different schema,
even though no such collision exists today.

**M1's guard checks table existence, not just function existence (found in a third review round).** The
original guard checked only whether `refresh_cyl_experiment_trait_counts()` (M2's function) still existed
in `pg_proc` — a narrower, less robust invariant than checking whether `cyl_experiment_trait_counts` (M2's
table) still exists. If the function were ever removed out-of-band (e.g. a manual `DROP FUNCTION` without
running M2's own rollback), a function-only check would see "absent" and let M1's rollback proceed even
though M2's table (and M3's RPC, if not yet rolled back) still depend on `cyl_scan_latest_source`
transitively. The guard now checks `EXISTS (... pg_proc ...) OR EXISTS (... pg_tables ...)` — the table is
the more durable signal that M2 hasn't actually been rolled back.

## Open Questions

- **D8 — refresh-scheduling host: resolved as a scheduled GitHub Action; interval settled at once
  daily, not the originally-proposed 5–15 min window.** `pg_cron` isn't installed in this stack. Two
  candidates were named: the `workflows` service (already running, would need new application code to
  poll on an interval) or a scheduled GitHub Action (`on: schedule`, calling the refresh function's
  PostgREST RPC endpoint with the `service_role` key — this repo already has scheduled Actions, e.g. the
  CVE-scan workflows on every PR, so the pattern is precedented, and it needs zero new application code).
  The **scheduled GitHub Action** shipped as the default (`.github/workflows/refresh-cyl-experiment-trait-counts.yml`).
  The interval was reconsidered after the workflow shipped: staging has low write volume and no caller
  currently depends on sub-daily freshness, so `cron: '0 6 * * *'` (once daily) is the actual schedule,
  not the 5–15 min window this design originally reasoned about above — `workflow_dispatch` still allows
  an on-demand manual run any time a fresher count is needed sooner. The workflow validates
  `STAGING_API_URL` starts with `https://` before making the call (added in the second review round) —
  a plain `http://` URL would send `SERVICE_ROLE_KEY` (a full-bypass-RLS credential) in cleartext on
  every scheduled run. `STAGING_API_URL` itself is hardcoded as a literal in the workflow rather than a
  GitHub secret — it's the same public, stable hostname already committed as `API_EXTERNAL_URL` in
  `.env.staging.defaults`, so there's nothing sensitive to store; `STAGING_SERVICE_ROLE_KEY` (the actual
  credential) already existed pre-change, so this schedule needs no secret provisioning at all
  (`tests/unit/test_refresh_workflow_shape.py` — renamed and broadened in round 9's redesign below —
  guards both facts).

  **Found in round 7: "resolved" only covers which mechanism runs the refresh, not whether it's
  actually live yet.** This PR's base branch is `staging`. GitHub Actions `schedule:` triggers only ever
  fire from the workflow file's copy on the repository's **default branch** — confirmed both as documented
  GitHub Actions behavior and against this repo's own precedent: `.github/workflows/promote-security-to-main.yml`
  states outright, "Scheduled runs use the workflow file on the default branch." `gh repo view` confirms
  `main` is this repo's default branch. So merging this PR lands `refresh-cyl-experiment-trait-counts.yml`
  on `staging` only — the daily cron stays inert, and `n_traits` staleness stays unbounded, until a later
  promotion PR carries the workflow file to `main`. `workflow_dispatch` (tasks.md 5.3) can be run against
  `staging` today to confirm the call logic itself works, but that's a materially different fact from "the
  automatic schedule is live," and no round before this one distinguished the two. Tracked as tasks.md 5.4
  (promotion to `main` confirmed) — a new, explicit pre-close gate for bloom#637, not implied by 5.3
  passing. (Round 8 correction: the promotion path is not the security bot's periodic sweep — that bot is
  scoped only to security/CVE commits and would never select this PR's own changes. It's this repo's
  separate, manual `staging -> main` promotion practice, run by @blm3886 roughly every 1-2 weeks — a
  faster, named path than "can sit there for weeks" implied.)

  **Found in round 8: branch promotion alone still doesn't close the gap — the workflow never targets
  production.** `STAGING_API_URL` is a hardcoded literal pointing at `staging.bloom.salk.edu`; production
  runs on a different host (`.env.prod.defaults`'s `API_EXTERNAL_URL=https://bloom.salk.edu/api`, a
  distinct Supabase instance), and no workflow anywhere in this repo ever calls
  `refresh_cyl_experiment_trait_counts()` against it. `deploy.yml`'s `deploy-production` job does apply
  this PR's migrations to production on push to `main` — including the one-time inline refresh call — so
  production's cache gets populated exactly once, at deploy time, and never again, independent of whether
  5.4's branch-promotion gate is ever satisfied. This is a stronger claim than "unbounded until promoted":
  for production specifically, it is permanently frozen unless a second, environment-aware refresh path is
  added.

  **Resolved (post-round-8, user-directed redesign): the production-targeting gap closed by dropping
  the schedule entirely; the promotion gap NOT actually closed by this — see the correction below.**
  The user's own framing cut through both findings at once — staging isn't going to need frequent
  automatic refreshes, so there's no reason to carry a `schedule:` trigger that (per the round-7
  finding) can't fire pre-promotion anyway. `on: schedule` was removed; the workflow shipped
  `workflow_dispatch`-only. The round-8 production gap is closed the same way `deploy.yml` already
  solves an identical problem: a `choice` input, `environment` (`staging`/`production`, mirroring
  `deploy.yml`'s own convention), selects which hardcoded URL/secret pair the run script resolves to
  (`PROD_API_URL`/`PROD_SERVICE_ROLE_KEY` added alongside the staging pair — `PROD_SERVICE_ROLE_KEY`
  already existed as a secret, so no new provisioning either).

  **Correction (bloom#708 investigation — this round's own claim about `workflow_dispatch` was wrong,
  confirmed against the live repo rather than assumed.)** This section originally claimed
  `workflow_dispatch`, "unlike `schedule:`, fires against _any_ branch/ref holding the file, no
  promotion required," and that this "fully closes 5.4 (nothing left to promote for this to work)."
  That is false. GitHub's own docs state plainly: "To trigger the `workflow_dispatch` event, your
  workflow must be in the default branch."
  (https://docs.github.com/en/actions/using-workflows/manually-running-a-workflow) — the exact same
  default-branch gate `schedule:` is subject to. The `ref`/`--ref` option a dispatch call accepts only
  selects which branch's *copy of the workflow's code* runs for that one invocation, once the workflow
  is already registered from the default branch; it does not make an unregistered workflow dispatchable
  from a non-default branch. Confirmed directly against this repo while this file existed only on
  `staging`: `gh api repos/.../actions/workflows/refresh-cyl-experiment-trait-counts.yml` returned `404
  Not Found`, and `gh workflow list --repo ... --all` did not list it at all — this workflow was, and
  is, completely undispatchable (no UI, no `gh workflow run`, no REST API call can trigger it) until it
  is promoted to `main`. Dropping `schedule:` for `workflow_dispatch`-only was still the right call on
  its own merits (staging's low write volume genuinely doesn't need an automatic cadence) — the error
  was specifically in believing that choice also sidestepped the promotion dependency. It didn't; both
  trigger types need this file on `main` before either can fire, manually or on a schedule. tasks.md 5.3
  (verify staging dispatch) and 5.6 (verify production dispatch) are corrected to say so explicitly, and
  neither is a "do this after merge" nicety anymore — both are genuinely blocked until this repo's next
  `staging -> main` promotion PR carries this workflow file forward.

  **D8 addendum (bloom#708, this section): an `on: schedule` cron trigger, scoped to `production`
  only.** Production is expected to eventually need an automatic refresh once its write volume grows
  past what on-demand dispatch can keep up with — bloom#708 tracks exactly this. Three design questions
  this raises, all resolved here:
  - **Target-host resolution.** A `schedule`-triggered run carries no `github.event.inputs` context at
    all (that context is populated only for `workflow_dispatch` events) — so the workflow can't read an
    `environment` choice off a schedule event the way it does for a manual dispatch. Resolved by
    branching on `github.event_name` instead: `ENVIRONMENT` (the step's env var, which drives the bash
    `case`/`esac` URL/key selection) and `concurrency.group`'s host suffix both use
    `${{ github.event_name == 'schedule' && 'production' || github.event.inputs.environment }}` — a
    scheduled run always resolves to `production`; a manual dispatch still requires the explicit input,
    for either host. `staging` keeps no automatic cadence at all, matching this design's original
    reasoning (low write volume) and bloom#708's own framing (only production needs this).
  - **The job's `environment:` key resolves differently — a second, ungated GitHub Environment for the
    scheduled path, found necessary only after checking the live approval-rule configuration rather
    than assuming round 9's gate would just work here too.** Round 9 added `environment:
    ${{ github.event.inputs.environment }}` specifically so a *human* dispatching this workflow can't
    fire an RLS-bypass RPC at production without a second person's approval — confirmed via the GitHub
    API that `production` carries `required_reviewers` and a 5-minute `wait_timer`. Naively reusing that
    same expression for a scheduled run (resolving to `environment: production`) would route every
    nightly run through that same approval gate — but GitHub Environment protection is keyed by
    environment *name*, not trigger type, so a scheduled run has no human present to click "Approve";
    it would sit "Waiting" indefinitely (or until GitHub's own multi-week protection-rule timeout),
    silently never executing. Worse, with `cancel-in-progress: true` sharing one concurrency group, the
    *next* night's run would cancel the still-pending prior one before anyone could approve it — a
    compounding failure that surfaces no error at all. The two trigger types have genuinely different
    risk shapes: `workflow_dispatch` lets any human with dispatch permission fire this RPC against
    production *at will, with parameters they choose in the moment* — the approval gate defends against
    exactly that discretion. A `schedule` trigger has no discretion: it always calls the same function,
    on the same fixed cron, against the same fixed host; the only way its behavior changes is by editing
    this workflow file, which already goes through this change's own OpenSpec review, ordinary PR review
    to merge to `staging`, and this repo's `staging -> main` promotion review before it can register at
    all (a schedule can't fire from an unpromoted file regardless — see the Correction above). A
    per-run human click adds no real protection against behavior that's already fully fixed by
    already-reviewed code. Resolved by introducing a second GitHub Environment,
    `production-scheduled-refresh`, with no protection rules at all, used only for `schedule`-triggered
    runs: `environment: ${{ github.event_name == 'schedule' && 'production-scheduled-refresh' ||
    github.event.inputs.environment }}` — note this is a **different expression** from the target-host
    one above (it names a distinct Environment for the schedule branch, not `'production'` itself);
    `workflow_dispatch` against either host is completely unchanged, still gated exactly as round 9 left
    it. `production-scheduled-refresh` needs no new secret provisioning — `PROD_SERVICE_ROLE_KEY` is a
    repository-level secret, not environment-scoped (confirmed in round 9), so it's visible to a job
    regardless of which Environment name the job references. GitHub auto-creates a referenced
    Environment with no protection rules on first use once the referencing workflow file is on the
    default branch, so no manual repository-settings step is needed either — it comes into existence as
    a side effect of this file's own promotion to `main`. `concurrency.group` stays keyed by the
    target-host expression (not this Environment-name one) so a scheduled production run and a manual
    `workflow_dispatch` production run still serialize against each other in the same group (both
    ultimately hit the same database, and the RPC's own `pg_advisory_xact_lock`, D5b, makes that safe);
    keying it by the Environment name instead would have let a pending-approval manual dispatch and an
    unrelated-group scheduled run race past each other with no serialization at all.

    **Accepted, not fixed — a narrower, lower-severity instance of the same cancellation shape this
    section already fixed for schedule-vs-schedule, found in round 2's re-verification of this fix.**
    Sharing one concurrency group by target-host means a manual `workflow_dispatch` to `production` that
    is currently sitting in "Waiting" for `required_reviewers` approval, and the 00:17 UTC scheduled run,
    both occupy the same group — confirmed via GitHub's own documented behavior that
    `cancel-in-progress: true` cancels a job that is queued/pending approval, not only one already
    executing. If the cron fires while a manual production dispatch is still pending approval, it
    silently cancels that pending dispatch. Unlike the schedule-vs-schedule case this section's main fix
    closes (which could compound into *no* run ever completing), this is a single, one-off cancellation
    of a specific manual request — safe to retry (D5b's advisory lock still makes any half-started state
    a clean no-op), and only reachable in the narrow window where someone manually dispatches production
    and a reviewer hasn't yet approved by the time the next cron tick lands. Not fixed here: giving the
    scheduled and manually-dispatched paths separate concurrency groups would reopen the very race this
    section's main fix (D5b's advisory lock aside) was written to prevent between two production-bound
    calls. A manual dispatcher whose run gets cancelled this way sees GitHub's own "cancelled" status,
    not a silent failure — an acceptable tradeoff, not an invisible one.

    **Accepted, not fixed — retroactive drift risk on `production-scheduled-refresh`'s own protection
    rules, found in round 2.** Nothing prevents a future repository-admin action from adding
    `required_reviewers` to `production-scheduled-refresh` after this section ships, silently
    reintroducing the exact stuck-forever bug this addendum exists to close — GitHub's Environment
    protection rules are configured independently of any workflow file, so no amount of YAML review can
    catch this by itself. Not worth a recurring automated check for a single-purpose Environment with no
    other consumer; tasks.md 14.9 folds one manual verification into the same post-promotion check this
    section already needs to do for another reason (confirming the scheduled job starts immediately),
    which is cheap enough to be worth doing without standing up dedicated monitoring for it.

    **Accepted, not fixed — `type: choice` is a UI-only constraint, not server-enforced, found by
    `/review-pr`'s security pass on the implemented code.** The `workflow_dispatch` input's `type:
    choice` restricts values in GitHub's web dispatch form, but the underlying REST/CLI API (what `gh
    workflow run -f environment=...` calls) accepts an arbitrary string. Traced character-by-character:
    a crafted dispatch with `environment=production-scheduled-refresh` makes `github.event_name ==
    'schedule'` false, so BOTH the job's `environment:` key and `ENVIRONMENT`/`concurrency.group`'s
    target-host expression fall through to the *same* literal `github.event.inputs.environment` value —
    `"production-scheduled-refresh"` for both. The job executes under the ungated Environment (a real,
    if narrow, approval-gate bypass for a human-triggered run), but the bash `case "${ENVIRONMENT}" in
    ...esac` only recognizes `staging`/`production` and rejects anything else via its `*)` branch before
    any `curl` call — so no RLS-bypass RPC call is actually reachable this way; the job errors out.
    Confirmed the two expressions can never diverge for a `workflow_dispatch` event specifically (both
    resolve to the identical raw input string), so this can't be combined with any other input to reach
    a real call under the ungated Environment. Not fixed with explicit allow-list validation given the
    bash guard already closes the actual RPC path — but this does mean the D8 addendum's own "manual
    dispatch... still gated exactly as round 9 left it" claim above is accurate for the RPC call itself,
    not for whether the job merely *executes* (harmlessly) under approval-gate bypass first.

    **Accepted, not fixed — two more residual risks found by `/review-pr`'s behavioral-correctness pass
    on the implemented code, distinct from the cancellation risks above.** (1) GitHub's own documented
    behavior allows a scheduled workflow run to be dropped entirely under high platform load — no run
    object is ever created, so there is nothing to cancel and no `timeout-minutes` budget is ever
    consumed; a missed 00:17 UTC tick simply doesn't happen, with zero visibility (unlike a cancelled run,
    which at least appears in the Actions UI with a status). Self-heals the next night, and is the same
    UI-lag tradeoff already accepted for `n_traits` generally (Goals/Non-Goals) — not a new class of risk,
    just an additional way the existing one can manifest. (2) This cron has no coordination with
    `deploy.yml`'s production deploy job (a separate, unrelated `concurrency.group: deploy-bloom`) — a
    migration altering the refresh RPC or locking underlying tables during a production deploy that
    happens to overlap 00:17 UTC could make that night's scheduled call fail. Low severity: the workflow
    already fails loudly (non-200/204 response -> `::error`, `exit 1`) rather than corrupting anything,
    and the next day's cron retries independently.

    **Accepted, not fixed — `list_available_experiments.py`'s `_STALE_AFTER = timedelta(days=2)`
    threshold is now a weak signal for the exact failure mode this section introduces, found by a
    second `/review-pr` round against the pushed PR.** With production's cache refreshing every ~24h,
    a *single* missed scheduled run only pushes a cached row's age to ~48h — right at the 2-day
    boundary — so the explicit staleness flag barely appears after one miss and stays silent through
    the entire first missed cycle. Not tightened here: `_traits_note()` has no way to tell which
    environment a given row came from (the same limitation already documented above), so a
    tighter threshold calibrated to catch a missed *production* refresh quickly would also false-alarm
    on staging's normal, harmless multi-day quiet periods. A real, if narrow, cross-environment tension
    this section doesn't resolve — worth a follow-up once (or if) the reader threads environment context
    through this call path, not a blocker for the schedule itself shipping.

  **D8 addendum, part 2 (Section 15, bloom#736) — the promotion/approval-gate story above was correct,
  but incomplete: the RPC call itself has never once reached either host.** This workflow's first live
  scheduled run, once actually promoted to `main` (2026-08-24/25), started executing immediately under
  `production-scheduled-refresh` exactly as designed above — and then failed in 12s:
  `curl: (28) Failed to connect to bloom.salk.edu port 443 after 5001 ms: Timeout was reached` (run
  32799136668, `2026-08-25T01:51:22Z`). Root cause: `runs-on: ubuntu-latest` (a GitHub-hosted runner) has
  no network route to the Salk server — a limitation `deploy.yml` already documents in its own comment
  ("GitHub-hosted runners have no route to the Salk server") for its self-hosted-runner jobs, but one
  this workflow never adopted, from PR #684's original introduction through this same section's own
  bloom#708 cron addition. This means every claim in this addendum above about the scheduled cron
  "working" was accurate only for the approval-gate/environment-resolution half — the actual delivery
  half was never exercised successfully by any run, scheduled or dispatched, at any point in this
  workflow's history.

  Fixed by changing `runs-on` to the same self-hosted label `deploy.yml` uses,
  `["self-hosted", "linux", "salk-network"]`, applied unconditionally (both hosts, all three trigger
  paths — this workflow has one job, not a per-host split, and the user chose parity over leaving
  staging on `ubuntu-latest` as a negative control). Deliberately not mirroring `deploy.yml`'s
  `ubuntu-latest` escape-hatch input: that input exists because `deploy.yml`'s jobs are required/blocking
  checks that need a way to fail fast when the self-hosted runner is hung; this job is neither, and the
  escape hatch would do nothing for the unattended `schedule` trigger anyway. Accepted, not mitigated: if
  `salk-network` is offline, a run now queues rather than failing in ~12s — the same trade-off
  `deploy.yml` already accepts for real deploys, and lower-stakes here since nothing blocks on this job.
  **Correction (`/review-openspec`'s CI/CD pass): the bound on this is NOT `concurrency.group`'s
  `cancel-in-progress: true`** — that flag only cancels an already-**running** job; GitHub's own
  documented default behavior already supersedes a **queued/pending** job in the same group once a newer
  trigger arrives, independent of `cancel-in-progress`. The actual bound differs by host: `production`'s
  daily cron supersedes a stuck queued run via that default behavior, on top of GitHub's own independent
  ~24h hard-cancel-queued-job limit; `staging` has nothing that re-triggers automatically, so a queued
  manual dispatch there relies solely on the same ~24h hard limit, not on anything this workflow's own
  concurrency configuration adds.

  **New risk, found by the same CI/CD pass, not previously documented anywhere in this design: this job
  now shares a runner pool with `deploy.yml`'s deploy jobs, which `deploy.yml`'s own
  `concurrency.group: deploy-bloom` comment states explicitly is a single physical machine ("single Salk
  server, single docker daemon"), not an autoscaling fleet.** The two workflows use different
  concurrency groups, so nothing serializes them against each other — a `deploy.yml` run
  (`timeout-minutes: 30`) can occupy the only matching runner while this job queues behind it for up to
  ~30 minutes, a distinct failure mode from "runner offline" that this addendum previously didn't
  mention. Accepted, not mitigated: no human is waiting synchronously on this job, and `timeout-minutes:
  2` only bounds execution time once a runner is assigned, not queued-for-a-runner time — both facts are
  called out explicitly in the workflow's own comment (tasks.md 15.3) rather than left for a future
  reader to assume incorrectly.

    **Reinforced, not newly found — round 2's `/review-pr` pass on the pushed PR independently
    converged on the same two follow-up angles for tasks.md 14.9's post-promotion check.** (1)
    Security's pass: confirming `production-scheduled-refresh` carries zero protection rules today
    isn't the same as confirming no *org-level* default environment-protection policy exists that
    would auto-attach `required_reviewers` to any newly-created Environment — check for an
    organization-wide policy specifically, not just this one Environment's own settings. (2)
    Behavioral-correctness's pass: the Environment's static configuration can be verified at any time,
    but the *first actual scheduled firing* only happens once, unattended, at an unscheduled future
    date (whenever the next `staging -> main` promotion lands) — checking Environment settings alone
    doesn't confirm that first live run actually succeeded end-to-end. tasks.md 14.9 now also calls for
    checking the Actions tab the morning after the first post-promotion midnight, not just the
    Environment's protection-rule config.
  - **Cron interval, reasoned from what's actually known, user-directed rather than benchmarked.**
    No real production write-cadence telemetry exists in this repo to derive a data-driven number from
    (confirmed by searching `bloommcp/docs/` and `services/workflows/` for any documented
    ingest-frequency figure — none found). The refresh itself costs a flat 6.6s regardless of write
    volume (D5), and the job has a 2-minute timeout with no meaningful resource cost, so there is no
    real cost pressure toward a long interval the way there might be for an expensive job. Once daily is
    the user's own chosen interval. The exact minute is `cron: '17 0 * * *'`, not a bare `'0 0 * * *'`
    — GitHub's own documentation flags the top of every hour, including midnight UTC, as a period of
    elevated scheduler load where a workflow "may be delayed" or occasionally dropped, and recommends
    picking a non-round minute; a fixed offset costs nothing and removes that risk entirely. This is a
    reasoned default, matching this design's own D7 precedent for reasoned-not-measured values: an
    estimate to revisit once real production write-cadence data exists, not a benchmarked figure.

  **Found in round 9: the redesign itself shipped with two real gaps, both since fixed.** (1)
  `concurrency.group` was a single static string shared by both environments — with
  `cancel-in-progress: true`, a `staging` dispatch and a `production` dispatch could cancel each
  other, even though they touch entirely independent databases and never actually race (each
  environment's own calls are already serialized by the RPC's own `pg_advisory_xact_lock`). Fixed
  by including `${{ github.event.inputs.environment }}` in the group name. (2) The job declared no
  `environment:` key at all, so it never went through this repo's GitHub Environment approval rules
  — confirmed via the GitHub API that both `staging` and `production` Environments here carry
  `required_reviewers` (and `production` also a 5-minute `wait_timer`), the same gate `deploy.yml`'s
  own staging/production jobs already opt into via their own job-level `environment:` key. Without
  it, anyone able to dispatch this workflow could fire an RLS-bypass RPC at either database — most
  concerningly production — with zero human approval, unlike every other path that reaches them.
  Fixed by adding `environment: ${{ github.event.inputs.environment }}` to the job (confirmed no
  environment-scoped secret shadows `STAGING_SERVICE_ROLE_KEY`/`PROD_SERVICE_ROLE_KEY`, so this adds
  the approval gate without changing which secret value resolves). Also removed the `environment`
  input's `default: 'staging'` — a dispatcher who forgets to change the dropdown before this fix
  would have silently refreshed staging while believing they'd refreshed production, with nothing
  surfacing the mistake; every dispatch now requires an explicit choice.

  **Also found in round 9 -- no named operational owner or cadence -- resolved by the user's own
  direction, not by adding process machinery.** `staging` needs neither an owner nor a fixed cadence
  at all: dispatch it manually, as needed, whenever testing calls for a fresher count -- unlike
  production, staging was never going to need a reliable automatic refresh in the first place (see
  the Context section's original framing of low staging write volume). `production` stays on-demand
  only until bloom#708's automatic-refresh follow-up ships; no interim manual-dispatch owner is being
  named for it either, since giving it a real automatic cadence is exactly what that issue is for,
  not a gap to paper over with an ad hoc human process in the meantime.

- **D7 — pinned-branch cost, not benchmarked.** See D7's own reasoning; needs a real `EXPLAIN (ANALYZE,
BUFFERS)` against staging once this lands, not resolved from this sandboxed pass.
- ~~`n_traits`'s `updated_at` isn't surfaced in `get_experiment_summary_counts`'s return shape.~~
  **Resolved in round 6** — see D5's own note above; `n_traits_updated_at` is now part of the RPC's
  return shape and `list_available_experiments`'s printed output.
- ~~Whether `cyl_scan_latest_source` and `cyl_experiment_trait_counts` need entries in the five tracked
  `database.types.ts` copies~~ **Resolved in round 4** — hand-edited into all five copies, matching PR
  #654's own precedent for this exact question.
