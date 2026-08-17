-- bloom#637 (supersedes PR #654's Phase 1): replace the live is_latest WindowAgg with a
-- stored, per-scan "latest source" table, joined into cyl_scan_traits_source instead of
-- recomputed on every read.
--
-- cyl_scan_latest_source holds one row per scan (25,264 rows against today's prod data) instead
-- of a boolean on every cyl_scan_traits row (28,786,885 rows) -- per @blm3886's review comment on
-- PR #654. A trigger keeps it current on every write to cyl_scan_traits (insert/update/delete,
-- covering both the write-back RPC and bloom_admin's break-glass access), guarded by
-- pg_advisory_xact_lock(scan_id) -- verified necessary (not the "no lock needed" claim in the
-- review comment) by reproducing a genuine two-writer staleness race against a local Postgres
-- during design; see openspec/changes/fix-cyl-scan-traits-latest-rollup/design.md D2 for the
-- reproduction.
--
-- The one-time backfill is a single aggregate query (~2.5s on prod, per Benfica's measurement),
-- run inside this same migration transaction -- not a batched, operator-invoked procedure like
-- PR #654's. CREATE TRIGGER below already takes a ShareRowExclusiveLock on cyl_scan_traits, held
-- for the rest of this transaction -- that alone blocks concurrent writers (but not readers: it
-- doesn't conflict with plain SELECT's AccessShareLock) for the backfill's duration, so no write
-- landing during the backfill can fall into the gap between "trigger exists but this write's
-- transaction predates it" and "backfill already ran". The explicit LOCK TABLE statement below is
-- redundant with that (confirmed empirically -- see design.md D3), kept only as self-documentation
-- of the safety property in case a future edit reorders these statements.
--
-- The view cutover (is_latest via a join instead of a window aggregate) lands in this same
-- migration, safe specifically because the LOCK TABLE step guarantees the backfill is complete
-- and no writer's data is unaccounted for by the time this commits.
--
-- Manual rollback: supabase/rollbacks/20260817010000_create_cyl_scan_latest_source_rollback.sql

BEGIN;

-- 1. Schema: one row per scan, not per trait row.
CREATE TABLE IF NOT EXISTS public.cyl_scan_latest_source (
    scan_id       bigint PRIMARY KEY REFERENCES public.cyl_scans(id) ON DELETE CASCADE,
    max_source_id bigint
);

ALTER TABLE public.cyl_scan_latest_source ENABLE ROW LEVEL SECURITY;

-- Matches cyl_scan_traits's own policy set exactly (20260506000001_bloom_role_rls_policies.sql +
-- its original 20231113203010 creation migration) -- permissive USING (true) for the same four
-- read roles this table's own GRANT below lists, since this table holds no information beyond
-- what cyl_scan_traits itself already exposes to those roles.
DROP POLICY IF EXISTS admin_all_cyl_scan_latest_source ON public.cyl_scan_latest_source;
CREATE POLICY admin_all_cyl_scan_latest_source ON public.cyl_scan_latest_source
    FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_scan_latest_source ON public.cyl_scan_latest_source;
CREATE POLICY agent_read_cyl_scan_latest_source ON public.cyl_scan_latest_source
    FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_scan_latest_source ON public.cyl_scan_latest_source;
CREATE POLICY user_read_cyl_scan_latest_source ON public.cyl_scan_latest_source
    FOR SELECT TO bloom_user USING (true);
DROP POLICY IF EXISTS authenticated_read_cyl_scan_latest_source ON public.cyl_scan_latest_source;
CREATE POLICY authenticated_read_cyl_scan_latest_source ON public.cyl_scan_latest_source
    FOR SELECT TO authenticated USING (true);

-- cyl_scan_traits_source is security_invoker -- a read role querying it executes this join AS
-- ITSELF, not as the view owner, so the read roles need direct SELECT on this table too.
GRANT SELECT ON public.cyl_scan_latest_source
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

-- RLS does NOT govern TRUNCATE (a Postgres limitation, not a policy gap) -- Supabase's default
-- privileges give anon/authenticated a raw TRUNCATE grant on every new public-schema table
-- regardless of the RLS policies above, confirmed exploitable directly (SET LOCAL ROLE anon;
-- TRUNCATE public.cyl_scan_latest_source; succeeded before this fix). Blast radius is
-- repo-wide, not scoped to this table: cyl_scan_traits_source INNER JOINs this table, so
-- truncating it would zero out is_latest for every scan, breaking get_scan_traits/
-- get_experiment_traits system-wide. Matches this repo's own precedent
-- (20260504000002_grant_all_scope_reduction.sql, which made the same fix for bloom_admin but
-- never extended it to anon/authenticated on any table -- confirmed anon can still TRUNCATE
-- cyl_scan_traits itself today, a pre-existing, repo-wide gap this migration does not attempt
-- to close beyond its own two new tables).
REVOKE TRUNCATE, REFERENCES, TRIGGER ON public.cyl_scan_latest_source FROM anon, authenticated;

-- 2. Trigger: per-row upsert guarded by an advisory lock. Writes to a DIFFERENT table than the
--    one it's triggered on, so it never re-fires itself -- no recursion guard needed (unlike a
--    same-table maintaining UPDATE would need).
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
    -- Reassigning a row's scan_id (an UPDATE where scan_id itself changes -- e.g. bloom_admin's
    -- break-glass access correcting a mis-attributed row) affects TWO scans, not one: COALESCE
    -- alone would only ever recompute NEW.scan_id (never NULL on an UPDATE), silently leaving
    -- OLD.scan_id's row stale -- potentially making every remaining row for that scan evaluate
    -- is_latest = false until an unrelated future write happens to touch it again. Found in
    -- review, not in the original pass; both scans are recomputed below when they differ.
    --
    -- Lock acquisition order is sorted (lower scan_id first), not NEW-then-OLD, so two concurrent
    -- cross-scan reassignments moving rows in opposite directions (txn 1: A->B, txn 2: B->A)
    -- can't deadlock each other by each locking their own "new" scan first.
    --
    -- A plain DELETE (v_new_scan IS NULL, v_old_scan IS NOT NULL) also takes THIS branch, not the
    -- ELSIF below -- IS DISTINCT FROM treats NULL as distinct from any non-NULL value, so it's
    -- always true for a DELETE. This is correct only because least()/greatest() ignore NULL
    -- arguments (collapsing v_lo/v_hi to the single real scan id, same as the ELSIF branch would
    -- lock), and the IF v_new_scan IS NOT NULL guard below correctly skips recomputing a
    -- nonexistent new scan. Found in round-4 review: fragile, not wrong -- a future edit assuming
    -- both v_lo/v_hi are real, distinct scan ids here (e.g. a log line naming both) would silently
    -- misbehave for every plain DELETE.
    IF v_old_scan IS NOT NULL AND v_old_scan IS DISTINCT FROM v_new_scan THEN
        v_lo := least(v_new_scan, v_old_scan);
        v_hi := greatest(v_new_scan, v_old_scan);
        PERFORM pg_advisory_xact_lock(v_lo);
        PERFORM pg_advisory_xact_lock(v_hi);
    ELSIF v_new_scan IS NOT NULL THEN
        -- Serializes concurrent writers to the SAME scan_id. Without this, two connections racing
        -- to upsert the same scan_id can converge to the WRONG max_source_id: `EXCLUDED` in an
        -- `ON CONFLICT DO UPDATE` is fixed at proposal time, before a conflict wait completes, and
        -- a fresh correlated subquery in the SET clause does not fix this either (Postgres fixes
        -- one snapshot for the whole statement before the wait, not after) -- both were reproduced
        -- empirically to go stale; only the lock closed it.
        PERFORM pg_advisory_xact_lock(v_new_scan);
    ELSE
        PERFORM pg_advisory_xact_lock(v_old_scan);
    END IF;

    IF v_new_scan IS NOT NULL THEN
        INSERT INTO public.cyl_scan_latest_source (scan_id, max_source_id)
        SELECT v_new_scan, max(source_id)
        FROM public.cyl_scan_traits
        WHERE scan_id = v_new_scan
        ON CONFLICT (scan_id) DO UPDATE SET max_source_id = EXCLUDED.max_source_id;
    END IF;

    IF v_old_scan IS NOT NULL AND v_old_scan IS DISTINCT FROM v_new_scan THEN
        INSERT INTO public.cyl_scan_latest_source (scan_id, max_source_id)
        SELECT v_old_scan, max(source_id)
        FROM public.cyl_scan_traits
        WHERE scan_id = v_old_scan
        ON CONFLICT (scan_id) DO UPDATE SET max_source_id = EXCLUDED.max_source_id;
    END IF;

    RETURN NULL;  -- AFTER trigger; return value is ignored
END;
$$;

-- Practically inert (Postgres refuses to invoke a RETURNS trigger function outside trigger
-- context, regardless of EXECUTE grants), but closed for consistency with every other
-- SECURITY DEFINER function this change adds -- a future audit scanning for "every new
-- SECURITY DEFINER function has the anon revoke" should not find this one as the sole exception.
REVOKE EXECUTE ON FUNCTION public.maintain_cyl_scan_latest_source() FROM PUBLIC, anon, authenticated;

-- CREATE OR REPLACE TRIGGER (PG14+; this repo runs PG15), not DROP TRIGGER IF EXISTS + CREATE --
-- found in a fourth review round: DROP TRIGGER IF EXISTS on a trigger that DOES already exist
-- (i.e. this migration being re-run, which this repo's own idempotency-test convention exercises)
-- takes AccessExclusiveLock, not ShareRowExclusiveLock -- confirmed via pg_locks and a concurrent
-- SELECT that blocked for the remainder of the re-run's transaction, contradicting this migration's
-- own "concurrent readers are unaffected" claim below. CREATE OR REPLACE TRIGGER replacing an
-- existing trigger takes only ShareRowExclusiveLock (confirmed the same way, concurrent SELECT
-- unblocked) -- same lock CREATE TRIGGER alone always took, on both a first application (no prior
-- trigger to replace) and a re-run (replacing the one this migration itself created).
CREATE OR REPLACE TRIGGER maintain_cyl_scan_latest_source_after_write
    AFTER INSERT OR UPDATE OR DELETE ON public.cyl_scan_traits
    FOR EACH ROW
    EXECUTE FUNCTION public.maintain_cyl_scan_latest_source();

-- 3. Redundant with the ShareRowExclusiveLock CREATE TRIGGER already took above and is still
--    holding (locks are held for the rest of the transaction, not released after the statement) --
--    kept as an explicit, self-documenting assertion of the safety property this migration relies
--    on, not as the mechanism itself. Any write-back call that lands during the backfill below
--    simply waits for this transaction to commit, then proceeds normally, now seeing the trigger
--    created above and computing against a snapshot that already includes this backfill's own
--    committed data. Concurrent readers are unaffected either way -- SHARE MODE (like
--    ShareRowExclusiveLock) conflicts with ROW EXCLUSIVE (INSERT/UPDATE/DELETE), not with
--    AccessShareLock (plain SELECT) -- verified against local Postgres's pg_locks, not assumed.
LOCK TABLE public.cyl_scan_traits IN SHARE MODE;

-- 4. Backfill -- one aggregate pass, not batched. ON CONFLICT makes this migration body
--    idempotent (safe to re-run), matching this repo's migration-idempotency test convention.
INSERT INTO public.cyl_scan_latest_source (scan_id, max_source_id)
SELECT scan_id, max(source_id) FROM public.cyl_scan_traits GROUP BY scan_id
ON CONFLICT (scan_id) DO UPDATE SET max_source_id = EXCLUDED.max_source_id;

-- 5. View cutover: is_latest via a join to the new table instead of a live window aggregate.
--    Same output as before for any given data -- same partition grain (scan_id), same
--    IS NOT DISTINCT FROM NULL handling. Every cyl_scan_traits row has exactly one matching
--    cyl_scan_latest_source row by construction (step 2's trigger creates one on the first write
--    to any scan_id; step 4's backfill covers every pre-existing one), so this inner join never
--    silently drops rows. No new index needed -- the join is on scan_id, already indexed
--    (idx_cyl_scan_traits, 20240828142957).
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

GRANT SELECT ON public.cyl_scan_traits_source
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

COMMIT;
