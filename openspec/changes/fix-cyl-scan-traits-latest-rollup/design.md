## Context

[bloom#637](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/637) — the timeout's two
costs are independent and both need fixing (Benfica's diagnosis, quoted in full below). Neither cost is
new: bloom#625's own design.md (Risks section) predicted both, calling the live `is_latest` computation
"not provably sub-second without benchmarking" and naming "an index on `cyl_scan_traits(is_latest,
scan_id)` or equivalent" as "the natural next step" if benchmarking showed it wasn't fast enough. This
change is that next step, expanded per Benfica's fuller diagnosis into a stored column (not just an
index — there is no column to index yet) plus a rollup table for the specific bulk/unpinned case.

Benfica's comment on #637 (2026-08-09), verbatim:

> Let's implement both, in this order.
>
> is_latest isn't stored anywhere. It lives on the cyl_scan_traits_source view. That means is_latest
> can't be indexed: there's no data on disk to build an index on. So every query that filters on
> is_latest has to first recompute.
>
> 1: move is_latest onto the table
> Make it a real stored column on cyl_scan_traits, maintained on write, with an index on it. Then find
> the last row becomes an index lookup. This fixes the cost for every reader of the view, not just this
> one RPC.
>
> The work: a one-time backfill, plus a trigger covering every write path that can change what "latest"
> means — new ingests, pipeline reruns, corrections, and the write-back RPC.
>
> Step 2: add the per-experiment rollup
>
> Even with an indexed is_latest, the all-experiments count still has to read ~26M latest rows, join up
> through scans → plants → waves → experiments, and group them. That's the other half of the timeout,
> and it's independent of how is_latest gets computed.
>
> So we still want the small summary table (experiment_id, n_plants, n_traits) for the browse path, kept
> current on a refresh.
>
> Step 2 can't stand on its own: refreshing the rollup means running the same expensive query, which
> today times out. Step 1 is what makes the refresh cheap enough to actually run. So step 1 first, step 2
> on top.
>
> Additionally, the counts query uses COUNT(DISTINCT ...), which forces a large sort per experiment.
> Rewriting it as a GROUP BY subquery avoids that sort.

**Correction made before this proposal was drafted, not left as an open question:** an earlier research
pass characterized `cyl_scan_traits_source.is_latest`'s per-`scan_id` (not per-`(scan_id, trait_id)`)
partition as a latent bug — "a rerun that only re-delivers a subset of traits silently un-latests the
undelivered older traits for that scan" — because bloom#625's own design.md Risks section had flagged
exactly that shape as a footgun. Reading the actual tests before drafting deltas surfaced that this is
tested, intended behavior, not a gap: `tests/integration/test_cyl_read_path.py:284`'s
`test_no_cross_source_mixing` (and its twin in `test_cyl_experiment_traits.py:169`) seeds precisely this
scenario — an older source wrote traits A+B, a newer source re-delivered only A — and asserts B is
**excluded**, commented `# not backfilled from the older source`. The live `cyl-trait-read` spec's
"Latest-source-by-default scan trait reads" requirement encodes the same rule as a named scenario. Storing
`is_latest` with a `(scan_id, trait_id)` partition instead would flip B back to `is_latest = true` (it
would become its own max within that narrower partition) — reintroducing the exact cross-source mixing
this repo already tests against. This design preserves the current per-`scan_id` grain unchanged.

## Goals / Non-Goals

- **Goals:** `list_experiments()` (and any future unpinned or single-experiment-pinned,
  no-source/run-override caller of `get_experiment_summary_counts`) returns in well under a second at
  staging's real scale; `is_latest` becomes a stored, indexed column whose value never disagrees with the
  view's current live computation, for any row, at any time (verified by the backfill's own completeness
  check, not assumed); every live write path to `cyl_scan_traits` — the write-back RPC and
  `bloom_admin`'s break-glass access — keeps `is_latest` correct without relying on the writer to know
  about it; the rollup table's contents always agree with what a fresh
  `get_experiment_summary_counts(experiment_id_)` live-join call would compute for that experiment, for
  every experiment that has ever been written to since the rollup was introduced.
- **Non-Goals:** re-deriving or changing `is_latest`'s selection semantics (see the partition-grain
  correction above — this is a storage change, not a behavior change). No new MCP tool, no
  `source_id_`/`run_id_` parameter threaded through any analysis tool (unchanged from bloom#625's own
  non-goals — this remains out of scope for the separate, future source-discovery effort). No RLS or
  write-grant change beyond what's strictly needed for the three new `SECURITY DEFINER` objects this
  change adds (D2's trigger function, D6's rollup-refresh function, and D7's extracted live-join helper
  both of those call — see each decision's own justification). No change to `get_experiment_traits`,
  `get_scan_traits`, or `list_experiment_trait_sources`'s own signatures — they benefit from the stored
  column transparently, without any change to their code.

## Decisions

### D1 — `is_latest`: stored column, unchanged partition grain, `NOT NULL DEFAULT false`

```sql
ALTER TABLE public.cyl_scan_traits
    ADD COLUMN is_latest boolean NOT NULL DEFAULT false;
```

A constant-default `ADD COLUMN` on Postgres 11+ is a metadata-only change (no table rewrite, no long
lock) — the expensive part is not adding the column, it's populating it correctly for 28.8M existing
rows (D4) and keeping it correct going forward (D2). `NOT NULL DEFAULT false` means every pre-existing
row is provisionally `false` until backfilled; see D4 for why this is safe (the view doesn't read this
column until the backfill is verified complete).

`is_latest`'s selection rule (what "latest" means, the `scan_id`-only partition grain, the
`IS NOT DISTINCT FROM` legacy-NULL handling) is unchanged from what the `cyl-trait-read` spec's
"Canonical source-aware trait view" requirement already normatively defines — see that requirement
rather than this paragraph for the rule itself; this decision only changes *where* the value lives (a
stored column instead of a per-query `WindowAgg`), not the rule.

### D2 — Trigger: `AFTER` per-row on `cyl_scan_traits`, guarded against recursive refire

A trigger on the table itself (not embedded in `insert_cyl_result_envelope`) is what covers both live
write surfaces without relying on either one to remember to maintain `is_latest` — `bloom_admin`'s
break-glass access (`supabase/migrations/20260506000001_bloom_role_rls_policies.sql:135-136`'s
`admin_all_cyl_scan_traits` policy) bypasses RLS but not table-level triggers.

**`SECURITY DEFINER` necessity, checked against actual roles, not assumed:** every role that can write
`cyl_scan_traits` today (`postgres`, via the write-back RPC's own `SECURITY DEFINER`; `bloom_admin`, via
its blanket `ALL` grant; `bloom_writer`, via its blanket `INSERT`/`UPDATE` grant —
`supabase/migrations/20260519130000_add_bloom_writer_role.sql:33`) already has privilege equal to or
exceeding what this trigger's own maintenance `UPDATE` needs as `SECURITY INVOKER`. So `SECURITY DEFINER`
is **not functionally required by any writer that exists today** — it's kept here defensively, in case a
future RLS policy change on `cyl_scan_traits` would otherwise block the trigger's own maintenance write
for some writer. **This is a real, if currently latent, privilege-widening surface**: `bloom_workflows`
(the one role in this repo with an intentionally *narrow*, column-scoped grant on this table —
`GRANT SELECT (scan_id, source_id)`, `supabase/migrations/20260730120000_create_cyl_pipeline_runs.sql:171`)
has no write grant today and so can't reach this trigger at all, but if a future writer role follows that
same narrow-grant pattern, a `SECURITY DEFINER` trigger would let it mutate `is_latest` beyond its own
grant without that role ever having been given that capability directly. Revisit this posture (drop
`SECURITY DEFINER` to `SECURITY INVOKER`, or explicitly scope it) if/when a narrower writer role is added.

```sql
CREATE OR REPLACE FUNCTION public.maintain_cyl_scan_traits_is_latest()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    affected_scan_id bigint := COALESCE(NEW.scan_id, OLD.scan_id);
BEGIN
    UPDATE public.cyl_scan_traits t
    SET is_latest = (t.source_id IS NOT DISTINCT FROM sub.max_source_id)
    FROM (
        SELECT max(source_id) AS max_source_id
        FROM public.cyl_scan_traits
        WHERE scan_id = affected_scan_id
    ) sub
    WHERE t.scan_id = affected_scan_id
      AND t.is_latest IS DISTINCT FROM (t.source_id IS NOT DISTINCT FROM sub.max_source_id);
    RETURN NULL;  -- AFTER trigger; return value is ignored
END;
$$;

CREATE TRIGGER maintain_is_latest_after_write
    AFTER INSERT OR UPDATE OR DELETE ON public.cyl_scan_traits
    FOR EACH ROW
    EXECUTE FUNCTION public.maintain_cyl_scan_traits_is_latest();
```

**Recompute is scoped to `scan_id`, not `(scan_id, trait_id)`** — a single write can change which
`source_id` is the max for the *whole scan*, affecting every trait row of that scan, not just the row
that was written (this is the same reasoning D1 preserves from the existing view). The `WHERE is_latest
IS DISTINCT FROM (...)` clause is a **recursion terminator, not an optimization**: this `UPDATE`
re-fires the same `AFTER UPDATE` trigger on every row it touches, but the second pass finds nothing left
to change (every row's `is_latest` already matches the recomputed value), so it updates zero rows and
the recursion ends after depth 2. This is a standard, load-bearing idiom for self-referential
trigger-maintained columns, not an incidental detail — an implementation that drops the `IS DISTINCT
FROM` guard (e.g. "simplify" the `WHERE` clause) will recurse until Postgres's `max_stack_depth` trips.

**Per-row granularity, not per-statement, matches how writes already happen**:
`insert_cyl_result_envelope` inserts one `cyl_scan_traits` row per trait per `INSERT ... VALUES (...)`
call in a loop (`supabase/migrations/20260706170000_cyl_writeback_contract_a3.sql:216`), not one bulk
multi-row `INSERT`. A per-row trigger firing N times for one envelope's N traits (each doing a full
scan-scoped recompute) is bounded by that scan's own trait count — small (tens), not the 26M-row scale
this change exists to avoid — and matches the existing write pattern exactly; a statement-level trigger
with a transition table would not reduce this cost given how writes are already shaped, and would add
complexity with no offsetting benefit.

### D3 — Index: partial index on `(scan_id) WHERE is_latest`, not a full composite index

```sql
CREATE INDEX idx_cyl_scan_traits_latest ON public.cyl_scan_traits (scan_id) WHERE is_latest;
```

**Storing the column, not the index, is what removes the 16.4s `WindowAgg` cost** — that cost is
recomputing `is_latest` from scratch on every read, which a stored boolean eliminates regardless of
selectivity. `is_latest` is ~90% selective across the whole table (≈26M of 28.8M rows) at current data
shape, so a full-table index on `is_latest` alone would rarely beat a sequential scan — the value of an
index here is serving the point-lookup pattern `get_experiment_traits`/`get_scan_traits`/
`list_experiment_trait_sources` already use (find the latest rows for *one* scan/experiment's small scan
set), where per-scan selectivity is what matters, not table-wide selectivity. The partial `WHERE
is_latest` form keeps the index roughly 90% the size of a full index while still serving that lookup
efficiently.

### D4 — Backfill: batched procedure, run outside the schema migration; view cutover deferred until verified complete

**The schema migration (D1/D2/D3) and the data backfill are deliberately two separate operations, not
one migration file.** A single `supabase db push` migration runs as one transaction; batching a 28.8M-row
`UPDATE` into bounded chunks only avoids a single long-held lock if each chunk commits independently,
which requires committing mid-procedure — not possible inside one implicit migration transaction.
Postgres 11+ supports `COMMIT` inside a `PROCEDURE` (not a `FUNCTION`) called via top-level `CALL`,
outside any enclosing transaction block:

```sql
CREATE OR REPLACE PROCEDURE public.backfill_cyl_scan_traits_is_latest(batch_size bigint DEFAULT 10000)
LANGUAGE plpgsql
AS $$
DECLARE
    lo bigint;
    hi bigint;
    max_scan_id bigint;
BEGIN
    SELECT min(scan_id), max(scan_id) INTO lo, max_scan_id FROM public.cyl_scan_traits;
    WHILE lo IS NOT NULL AND lo <= max_scan_id LOOP
        hi := lo + batch_size - 1;
        UPDATE public.cyl_scan_traits t
        SET is_latest = (t.source_id IS NOT DISTINCT FROM sub.max_source_id)
        FROM (
            SELECT scan_id, max(source_id) AS max_source_id
            FROM public.cyl_scan_traits
            WHERE scan_id BETWEEN lo AND hi
            GROUP BY scan_id
        ) sub
        WHERE t.scan_id = sub.scan_id AND t.scan_id BETWEEN lo AND hi;
        COMMIT;
        lo := hi + 1;
    END LOOP;
END;
$$;
```

Batched by `scan_id` ranges (not raw `id`/PK ranges) so every row for a given scan lands in the same
batch — the `max(source_id)` grouping is only correct if a scan's rows aren't split across batch
boundaries. Idempotent and resumable: re-running recomputes deterministically from current state, so a
failed/interrupted run can just be re-invoked from `min(scan_id)` again (cheap — already-correct rows
are unconditionally re-set to the same value, no `WHERE ... IS DISTINCT FROM` needed here since this runs
once, not repeatedly like D2's trigger).

**`batch_size` is a `scan_id`-*range width*, not a row count** — `hi := lo + batch_size - 1` operates on
`scan_id`, and a batch's actual row count depends on how many `cyl_scan_traits` rows exist per scan
(D2 estimates "tens" per scan) times however many `scan_id`s fall in a 10,000-wide range. This is easy to
misread as "10,000 rows per batch," which it is not — naming it `batch_size` invites exactly that
misreading. **Before this procedure is run against staging, get real numbers**: staging's actual distinct
`cyl_scan_traits.scan_id` count (not just the 28.8M row count already known), and a timed dry run against
a representative-scale local fixture, so the batch count and expected wall-clock time are known
quantities, not assumed. Neither number is available from this sandboxed proposal-drafting environment —
tasks.md carries this as an explicit pre-run step, not a blocking proposal-approval item.

**Correctness ordering, not just a lock-avoidance detail:** `cyl_scan_traits_source`'s view definition
keeps computing `is_latest` **live** (unchanged) until a separate, later migration cuts it over to read
the new stored column — only after the backfill has run to completion and a verification query (task
list, section 3) confirms the stored column agrees with the live computation for 100% of rows. Cutting
the view over before the backfill finishes would make every reader see `is_latest = false` for any
not-yet-backfilled row (the column's `DEFAULT false`), silently under-reporting "latest" data — a real
regression, not a transition-period rounding error. This ordering is why D1's column addition and the
view's read-path cutover are separate migrations, not one.

### D5 — `cyl_experiment_summary_counts` rollup: schema and backfill

```sql
CREATE TABLE public.cyl_experiment_summary_counts (
    experiment_id bigint PRIMARY KEY REFERENCES public.cyl_experiments(id) ON DELETE CASCADE,
    n_plants      int NOT NULL,
    n_traits      int NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
```

Only experiments with at least one matching trait row get a row — mirroring
`get_experiment_summary_counts`'s existing "absent if zero, not zero-valued" contract exactly (the live
spec's own "An experiment with no matching trait rows is absent, not zero-valued" scenario), so
`list_experiments()`'s existing zero-default merge in Python needs no change: it already treats a
missing `experiment_id` as `n_plants=0, n_traits=0`. Populated by a one-time backfill (`INSERT INTO
cyl_experiment_summary_counts SELECT ... FROM <the D6 aggregate query> WHERE experiment_id_ IS NULL`,
run once the `is_latest` backfill (D4) is complete — this is the concrete sense in which "step 2 can't
stand on its own" per Benfica's comment: this backfill's own aggregate is exactly the query that times
out today, and only becomes runnable once `is_latest` is an indexed column).

### D6 — Rollup maintenance: event-driven, scoped to one experiment — flagged for Benfica, not assumed silently

**Benfica's comment says the rollup is "kept current on a refresh" without naming the mechanism.** Two
readings are both consistent with her words: (a) a scheduled/periodic refresh of the whole table
(matching the issue's own "periodic-refresh materialized view" framing as an alternative she was
choosing between), or (b) an event-driven update scoped to just the experiment that changed, piggybacking
on D2's trigger. This design implements **(b)**:

```sql
CREATE OR REPLACE FUNCTION public.refresh_cyl_experiment_summary_counts_for_scan(p_scan_id bigint)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    exp_id bigint;
BEGIN
    SELECT cyl_experiments.id INTO exp_id
    FROM public.cyl_scans
    JOIN public.cyl_plants ON cyl_plants.id = cyl_scans.plant_id
    JOIN public.cyl_waves ON cyl_waves.id = cyl_plants.wave_id
    JOIN public.cyl_experiments ON cyl_experiments.id = cyl_waves.experiment_id
    WHERE cyl_scans.id = p_scan_id;

    IF exp_id IS NULL THEN
        RETURN;  -- scan not reachable from any experiment (e.g. plant has no accession — see D7)
    END IF;

    -- Delete-then-reinsert (not UPDATE) so an experiment that drops to zero matching rows
    -- disappears from the rollup, matching D5's "absent if zero" contract.
    DELETE FROM public.cyl_experiment_summary_counts WHERE experiment_id = exp_id;
    INSERT INTO public.cyl_experiment_summary_counts (experiment_id, n_plants, n_traits, updated_at)
    SELECT experiment_id, n_plants, n_traits, now()
    FROM public.compute_cyl_experiment_summary_counts_live(exp_id, NULL, NULL)  -- see D7's helper
    ;
END;
$$;
```

D2's trigger calls this function once per affected `scan_id` after it finishes maintaining `is_latest`
(same `AFTER` trigger, or a second trigger on the same table — implementation task, not a design
question). This is cheap specifically because `is_latest` is now indexed (D1-D3) — recomputing one
experiment's counts touches only that experiment's own scans, which is the whole reason M1
(D1-D3) gates M3 (D5-D6). Note this calls `compute_cyl_experiment_summary_counts_live` — the
extracted live-join helper D7 defines — directly, never the rollup-backed
`get_experiment_summary_counts` RPC itself; see D7's "self-reference" note for why that distinction is
load-bearing, not stylistic.

This is the one point in Benfica's comment left unconfirmed — see Open Questions for the resolution and
the reasoning; not re-derived here.

### D7 — `get_experiment_summary_counts`: rollup-backed when no source/run override, live join otherwise

**The live-join aggregation is its own named, `SECURITY DEFINER` helper — not inlined, not a placeholder.**
This is the third `SECURITY DEFINER` object this change adds (alongside D2's trigger and D6's refresh
function), given its own concrete definition here rather than left as prose about "an extracted helper":

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
        SELECT cyl_experiments.id AS experiment_id, cyl_plants.id AS plant_id, src.trait_name
        FROM public.cyl_experiments
        JOIN public.cyl_waves       ON cyl_waves.experiment_id = cyl_experiments.id
        JOIN public.cyl_plants      ON cyl_plants.wave_id = cyl_waves.id
        JOIN public.accessions      ON cyl_plants.accession_id = accessions.id
        JOIN public.cyl_scans       ON cyl_scans.plant_id = cyl_plants.id
        JOIN public.cyl_scan_traits_source src ON src.scan_id = cyl_scans.id
        WHERE (experiment_id_ IS NULL OR cyl_experiments.id = experiment_id_)
          AND ( (source_id_ IS NULL AND run_id_ IS NULL AND src.is_latest)
             OR (source_id_ IS NOT NULL AND src.source_id = source_id_)
             OR (run_id_ IS NOT NULL AND src.source_id = (
                    SELECT max(s2.source_id) FROM public.cyl_scan_traits_source s2
                    WHERE s2.scan_id = src.scan_id AND s2.trait_id = src.trait_id
                      AND s2.pipeline_run_id = run_id_)) )
    ),
    plant_counts AS (
        SELECT experiment_id, count(*) AS n_plants
        FROM (SELECT DISTINCT experiment_id, plant_id FROM matched) d
        GROUP BY experiment_id
    ),
    trait_counts AS (
        SELECT experiment_id, count(*) AS n_traits
        FROM (SELECT DISTINCT experiment_id, trait_name FROM matched WHERE trait_name IS NOT NULL) d
        GROUP BY experiment_id
    )
    SELECT p.experiment_id, p.n_plants, COALESCE(t.n_traits, 0)
    FROM plant_counts p
    LEFT JOIN trait_counts t ON t.experiment_id = p.experiment_id;
END; $$;

REVOKE EXECUTE ON FUNCTION public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;
```

This helper accepts the same three-way disjunction `get_experiment_traits` uses (unpinned-latest via
`src.is_latest`, or pinned `source_id_`, or pinned `run_id_`) so **both** D6's refresh (which always
calls it with `source_id_ = NULL, run_id_ = NULL`, i.e. the unpinned-latest branch, scoped to one
`experiment_id_`) and D7's own source/run-pinned branch below reuse the identical join chain and
`GROUP BY` subquery rewrite — there is exactly one place this logic is defined, not two copies drifting
apart over time.

```sql
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
        -- "Current latest" case (pinned to one experiment, or every experiment) — read the rollup.
        RETURN QUERY
        SELECT c.experiment_id, c.n_plants, c.n_traits
        FROM public.cyl_experiment_summary_counts c
        WHERE experiment_id_ IS NULL OR c.experiment_id = experiment_id_;
        RETURN;
    END IF;

    -- source_id_/run_id_ pin: rollup doesn't cover arbitrary historical pins; delegate to the shared helper.
    RETURN QUERY
    SELECT * FROM public.compute_cyl_experiment_summary_counts_live(experiment_id_, source_id_, run_id_);
END; $$;
```

**Why the rollup also serves the pinned-no-override case, not just the unpinned bulk case:** a call with
`experiment_id_` set and `source_id_`/`run_id_` both `NULL` computes exactly the same "current latest"
counts the rollup already stores for that one experiment — no caller does this today (`list_experiments()`
is the RPC's only caller, always unpinned), but there's no reason to run the expensive live join for a
case the rollup already answers correctly.

**Self-reference, avoided by construction, not by convention:** D6's refresh function calls
`compute_cyl_experiment_summary_counts_live` directly — never `get_experiment_summary_counts` itself.
This matters because D6 fires from the same trigger that maintains `is_latest`: if the refresh called
the rollup-backed RPC branch, it would read `cyl_experiment_summary_counts` before this very refresh has
written it — a read-before-write ordering bug. Because the helper is a separate, independently-callable
function (not logic duplicated inline in two places, and not a branch of `get_experiment_summary_counts`
that D6 would have to know to route around), this can't happen by accident — there is no code path by
which D6 reaches the rollup-backed branch at all.

**`COUNT(DISTINCT ...)` → `GROUP BY` subquery, per Benfica's "additionally" note:** the rewrite computes
distinct `(experiment_id, plant_id)`/`(experiment_id, trait_name)` pairs via a `SELECT DISTINCT` subquery
(dedup via `HashAggregate`) before counting per `experiment_id` (a second `GROUP BY`), instead of a
single-pass `COUNT(DISTINCT col) ... GROUP BY experiment_id` (which Postgres's planner typically
implements via a per-group `Sort` + `Unique`, not a `HashAggregate`, when combined with `GROUP BY`).
`trait_name IS NOT NULL` is filtered explicitly in the trait subquery — a plain `SELECT DISTINCT` does
not ignore `NULL`s the way `COUNT(DISTINCT trait_name)` does, so without this filter a legacy row with an
unresolved `trait_id` would be double-counted as a phantom "trait." Verify via `EXPLAIN (ANALYZE,
BUFFERS)` that the rewritten plan has no `Sort` node feeding the aggregate, mirroring bloom#637's own
`EXPLAIN`-driven diagnosis methodology (tasks.md §6).

### D8 — Backfill invocation: this is a new deployment primitive, not a "PR description" footnote

**This does not fit safely as manual-step prose inside one PR.** Staging's deploy workflow
(`.github/workflows/deploy.yml`) triggers on every push to `staging` and runs `supabase db push`
unconditionally over **every pending migration** in one shot — there is no per-migration content gate, no
mechanism for "apply migration N, pause for a human, then apply migration N+1." If the view-cutover (M4)
or RPC-rewrite (M5/D7) migrations ship in the same PR/merge as their prerequisite backfill, `db push` applies
all of them back-to-back with zero wall-clock gap for an operator to run `CALL
backfill_cyl_scan_traits_is_latest();` in between — the exact failure mode D4 exists to prevent (the view
would cut over while ~26M rows are still `is_latest = false`, and/or the RPC would read an empty rollup),
producing a worse regression than today's timeout, not a lateral one.

Separately: `CALL`-ing a procedure requires a direct `psql`/libpq connection — **PostgREST cannot invoke
`CALL`**, only `SELECT function(...)`, so this backfill cannot be run through any Supabase client or REST
path. Postgres is bound to loopback only (per the migration-runner infrastructure), so running this
requires SSH access to the server. This repo's own deploy-migration policy states, verbatim: *"Migrations
to production and staging are applied EXCLUSIVELY through the GitHub Actions deploy workflow. Do not
manually run `supabase db push` or any migration command via SSH on the server except for documented
emergency recovery. Any emergency manual operation must be logged on an incident ticket."* Running this
backfill is exactly the kind of manual SSH+`psql` operation that policy currently scopes to emergencies —
this proposal does not have the authority to unilaterally carve out an exception to that policy, and
doesn't try to. **This is flagged as an open item for whoever owns deploy policy (Benfica, going by her
role in this area) to resolve — see Open Questions — not resolved unilaterally here.** Two ways it could
resolve, either acceptable to this design: (a) a narrowly-scoped, reviewed, one-time exception logged the
same way an emergency operation would be, explicitly bounded to this backfill's `CALL`; or (b) a small,
reviewed connection-wrapper script (e.g. `scripts/run_cyl_scan_traits_backfill.sh`, opening a direct
`psql` session with default autocommit — no explicit `BEGIN`, since `CALL`'s internal `COMMIT`s require
not already being inside a transaction block) added as part of this change's own scope, if a repeatable
tool is preferred over a one-off logged exception.

## Migration Plan

**Naming note to avoid a real ambiguity:** this section labels the five migrations **M1-M5**, deliberately
not "step 1-5" — `tasks.md` has its own numbered sections (0-8) covering this same work plus
tests/docs/runbook, and an earlier draft of this document used bare "step N" for both, which meant
"step 4" here and "section 4" in `tasks.md` referred to two completely unrelated things (this document's
view-cutover migration vs. `tasks.md`'s operator-runbook section). The `M`-prefix also keeps these
visually distinct from this document's own `D1`-`D8` decision numbers. Every cross-reference to
`tasks.md` below is written as `tasks.md §N` to keep the two documents' numbering schemes apart even
where the numbers themselves might otherwise coincide.

**Landing plan: two PRs against this one OpenSpec change, not one — this is the direct consequence of
D8.** Phase 1 is fully additive/inert (nothing reads the new column or table until Phase 2 lands) and is
safe to auto-deploy the moment it merges. Phase 2 (the view cutover and the RPC rewrite) must not merge
until the runbook between the phases has completed — merging it early is the regression D8 describes.

Exact filenames/timestamps are finalized against `staging`'s actual tip immediately before opening each
PR, per this repo's own known same-day-collision precedent — `staging`'s newest migration as of this
proposal is `20260807000000_get_experiment_summary_counts.sql`.

**Phase 1 PR** (`tasks.md` §0-3):

- **M1 — Schema**: `ADD COLUMN is_latest` (D1) + trigger function/trigger (D2) + partial index
  (D3). Forward migration only adds objects; view (`cyl_scan_traits_source`) is untouched — still
  computes `is_latest` live. Companion rollback drops the trigger, index, and column, in that order (no
  `CASCADE` — see the Rollback Ordering note below).
- **M2 — Backfill procedure definition**: create the `backfill_cyl_scan_traits_is_latest`
  procedure (D4) as its own migration (the procedure *definition* is schema, additive, safely
  re-runnable, and inert until invoked — this migration itself is safe to auto-deploy). It does **not**
  `CALL` the procedure.
- **M3 — Rollup table + maintenance**: `CREATE TABLE cyl_experiment_summary_counts` (D5) + the
  `compute_cyl_experiment_summary_counts_live` helper and `refresh_cyl_experiment_summary_counts_for_scan`
  function (D6/D7) + attaching the refresh's invocation to the same trigger from M1 (or a
  second trigger on `cyl_scan_traits`, implementation's choice — see `tasks.md` for the test that must
  pass regardless of which). Inert: nothing reads this table until Phase 2's RPC rewrite lands, so new
  writes populate it correctly from the moment this merges, but it isn't consulted by anything yet. Its
  own one-time backfill procedure is defined here too, for the same reason as M2 — not invoked
  yet.

**Operator runbook** (between the two PRs, per D8; `tasks.md` §4 — not a commit in either PR):

a. Run `CALL backfill_cyl_scan_traits_is_latest();` on staging.
b. Run the completeness-verification query (`SELECT count(*) FROM cyl_scan_traits WHERE is_latest !=
   <live computation>` — expect `0`). Do not proceed until this returns `0`.
c. Run the rollup's own one-time backfill procedure (batched by `experiment_id`, same reasoning as (a)).
d. Only once (a)-(c) are complete and verified does the Phase 2 PR open.

**Phase 2 PR** (code-only against the same OpenSpec change; `tasks.md` §5-6):

- **M4 — View cutover**: `CREATE OR REPLACE VIEW cyl_scan_traits_source` — same columns,
  `is_latest` now `cst.is_latest` (the stored column) instead of the `WindowAgg` expression. Companion
  rollback restores the live-computation view definition.
- **M5 — RPC rewrite**: `CREATE OR REPLACE FUNCTION get_experiment_summary_counts` (D7),
  delegating to M3's `compute_cyl_experiment_summary_counts_live` helper for its
  source/run-pinned branch. Additive (`CREATE OR REPLACE`, same signature and grants as today — no
  `REVOKE`/`GRANT` change needed for the RPC itself; the helper gets its own grants, per D7's SQL).

**Rollback ordering — not symmetric across migrations, stated explicitly rather than assumed:**
M1's column and M4's view are protected by Postgres's own catalog dependency tracking
(`CREATE VIEW` registers a hard `pg_depend` edge on the columns it reads) — rolling back M1
while M4 is live fails loudly (`cannot drop column ... because other objects depend on it`),
**as long as no rollback script adds `CASCADE`** (none should; this is a rule, not an accident, and
rollback scripts MUST NOT use `CASCADE` for exactly this reason). M3 and M5 (the rollup table
and the RPC/helper that read it) have **no such protection** — a `PL/pgSQL` function body referencing a
table is opaque to Postgres's dependency tracker, so rolling back M3's table while M5's
function still reads it will succeed silently at `DROP TABLE` time and only fail later, at runtime,
on the next call (`relation ... does not exist`). Any rollback must be applied in strict reverse order
(M5 before M3, M4 before M1) — this is an operational discipline this design relies on, not something the
schema itself enforces for every migration.

## Risks / Trade-offs

- **The backfill and rollup-population steps are the least exercised part of this design in code
  review** — batched, `COMMIT`-inside-`PROCEDURE` backfills have no precedent anywhere in this repo's
  `supabase/migrations/` (confirmed: no `CREATE PROCEDURE` exists in the tracked tree today; every prior
  migration is a single-transaction `CREATE OR REPLACE FUNCTION`/`CREATE TABLE`/`ALTER TABLE`). This is
  new operational surface — a batch-size choice, resumability, and a completeness-verification query all
  need real testing against a large local fixture (tasks.md §3), not just unit-tested SQL logic.
- **The test connection used for backfill tests can't be the default `pg_conn` fixture.** `CALL`-ing a
  procedure that issues internal `COMMIT`s requires the session not already be inside an explicit
  transaction block; `tests/integration/conftest.py`'s `pg_conn` opens one implicitly on first use
  (confirmed by `test_migrations.py`'s own comment on psycopg3's `autocommit=False` default). Backfill
  tests need a dedicated autocommit connection — `conftest.py` already provides a `pg_conninfo` fixture
  for exactly this shape of test — and must clean up via explicit `DELETE`, not `pg_conn.rollback()`,
  since anything the backfill touches is already committed. tasks.md §3 states this explicitly.
- **D6's event-driven, per-experiment refresh is this design's own choice, not confirmed by Benfica** —
  see Open Questions.
- **The trigger recursion guard (D2) is subtle and easy to "simplify" incorrectly** — removing the `WHERE
  is_latest IS DISTINCT FROM (...)` clause from the maintenance `UPDATE` (e.g. during a future refactor
  that doesn't understand why it's there) reintroduces infinite trigger recursion. Flagged explicitly so
  a future edit doesn't drop it as apparently-redundant.
- **No existing precedent in this repo for a trigger that maintains a *different* table (the rollup)** —
  every existing trigger (`set_created_by`, `update_chat_threads_updated_at`, `notify_video_job`) mutates
  only the row being written or sends a notification; none writes to a separate table. D6 is new pattern
  territory for this codebase, which is exactly why this design spells out the recursion/ordering
  concerns in detail rather than treating it as a routine trigger.
- **`is_latest`'s own selectivity (~90% latest) means the partial index (D3) helps point lookups, not
  table-wide scans** — already covered in D3; noted here as a reviewer-facing reminder that "we added an
  index" is not, by itself, the fix — the stored column removing the `WindowAgg` recomputation is what
  fixes cost (1); the rollup removing the join/`GROUP BY` at bulk scale is what fixes cost (2). Neither
  fix is the other's substitute, matching Benfica's own framing ("that's the other half of the timeout,
  and it's independent of how is_latest gets computed").

## Open Questions

- **The Migration Plan's Phase 2 needs re-sequencing — discovered during Phase 1 implementation,
  not resolved here.** `cyl_scan_traits_source.is_latest` stays live-computed (the `WindowAgg`)
  until M4 lands — meaning `compute_cyl_experiment_summary_counts_live` (which joins through that
  view, not the stored column) is correct regardless of the stored column's backfill state, for as
  long as M4 hasn't cut over. That's good for Phase 1 (the rollup's event-driven refresh is
  already correct the moment M3 merges), but it means the rollup *backfill*'s cost is not actually
  reduced until M4 is live — Benfica's "step 1 is what makes the refresh cheap enough to run"
  claim is about M4 (the view reading the indexed column), not M1-M3. As originally sequenced
  (operator runbook step (c), the rollup backfill, running *before* Phase 2/M4 even opens), the
  rollup backfill would still pay the full live-`WindowAgg` cost — the exact cost this whole
  change exists to remove. Correct ordering is a three-way constraint: **M4 (view cutover) →
  rollup backfill (now cheap) → M5 (RPC rewrite)** — and because staging's deploy workflow applies
  every migration in one PR's merge unconditionally (the same reasoning as D8), M4 and M5 likely
  can't safely land in the same PR either, the same way M1-M3 and M4/M5 can't. **This likely means
  a three-PR landing plan, not two** (Phase 1 as scoped here; Phase 2 = M4 only + a rollup-backfill
  runbook window; Phase 3 = M5 only). Not fixed in this pass because it only affects Phase 2/3
  sequencing, which this implementation pass doesn't touch — Phase 1 (M1-M3, this document's
  actual scope right now) is unaffected and already verified correct against this exact
  live-view behavior (see `tests/integration/test_cyl_experiment_summary_rollup.py`'s skipped
  `test_rollup_backfill_ordering_gate_consequence`, which documents this finding in place). Revise
  the Migration Plan's Phase 2 section before starting Phase 2 implementation.
- **D6's refresh mechanism (event-driven vs. scheduled)** — the one point in Benfica's comment this
  design had to resolve without her explicit confirmation. Flagged in the PR description for her review,
  per this repo's established D1/D5 precedent (ship a considered default, confirm via review).
- **D8's backfill-invocation policy conflict — genuinely unresolved, needs a deploy-policy owner's
  decision, not just a documented default.** Unlike D6 (where either answer leaves the rest of the design
  intact), this one gates whether Phase 1's runbook can execute at all. Flagged explicitly in the Phase 1
  PR description; the runbook step (D8's operator runbook) should not run until this is resolved one way
  or the other.
- **Whether `cyl_experiment_summary_counts` needs its own generated-types entry** in the five tracked
  `database.types.ts` copies — bloom#625's `get_experiment_summary_counts` needed this because it's an
  RPC signature; a plain table with no TypeScript caller may not need the same treatment. Resolve during
  implementation (tasks.md carries an explicit task for this) by checking whether `supabase gen types`
  output actually changes when run against a local DB with this migration applied — not assumed either
  way in proposal.md.
- **Whether the D6 rollup-refresh invocation is wired as a second statement inside D2's trigger function,
  or as a second, separate trigger on the same table** — left as an implementation choice in M3, but not
  a free one: if implemented as two separate triggers, Postgres fires same-event triggers in trigger-name
  alphabetical order, so the refresh trigger's name must sort after the `is_latest`-maintenance trigger's
  name, or the rollup would be refreshed from stale `is_latest` values. `tasks.md` §3.5 carries a test
  that must pass regardless of which implementation is chosen.
- **D8's second resolution path (a connection-wrapper script) has no task tracking it yet.** If the
  policy question resolves to "write a repeatable script" rather than "log a one-time exception,"
  `tasks.md` §0.3 must be expanded at that point to add the script itself, a review of it, and a
  manual/documented check of its connection semantics (bare `psql`, default autocommit, no explicit
  `BEGIN`) before it's used against staging — not created ad hoc when someone reaches for it. Not added as
  a concrete task now because it's conditional on which resolution path D8's policy question actually
  takes; flagged here so it isn't forgotten if that's the path chosen.
