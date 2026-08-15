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
-- Manual rollback: supabase/rollbacks/20260814010000_create_cyl_scan_latest_source_rollback.sql

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
    affected_scan_id bigint := COALESCE(NEW.scan_id, OLD.scan_id);
BEGIN
    -- Serializes concurrent writers to the SAME scan_id. Without this, two connections racing
    -- to upsert the same scan_id can converge to the WRONG max_source_id: `EXCLUDED` in an
    -- `ON CONFLICT DO UPDATE` is fixed at proposal time, before a conflict wait completes, and a
    -- fresh correlated subquery in the SET clause does not fix this either (Postgres fixes one
    -- snapshot for the whole statement before the wait, not after) -- both were reproduced
    -- empirically to go stale; only the lock closed it. Scoped to one scan_id, so this never
    -- contends with a write to a different scan. NOTE: advisory locks participate in Postgres's
    -- deadlock detector -- a single transaction that ever touches multiple scan_ids could deadlock
    -- against another transaction acquiring the same scan_ids in the opposite order. Today's sole
    -- writer (insert_cyl_result_envelope) is single-scan-per-call, so this is dormant; a future
    -- multi-scan batch writer would need to acquire scan_id locks in a consistent (e.g. sorted)
    -- order to stay safe.
    PERFORM pg_advisory_xact_lock(affected_scan_id);

    INSERT INTO public.cyl_scan_latest_source (scan_id, max_source_id)
    SELECT affected_scan_id, max(source_id)
    FROM public.cyl_scan_traits
    WHERE scan_id = affected_scan_id
    ON CONFLICT (scan_id) DO UPDATE SET max_source_id = EXCLUDED.max_source_id;

    RETURN NULL;  -- AFTER trigger; return value is ignored
END;
$$;

-- Postgres has no CREATE TRIGGER IF NOT EXISTS; DROP+CREATE keeps this migration re-runnable.
DROP TRIGGER IF EXISTS maintain_cyl_scan_latest_source_after_write ON public.cyl_scan_traits;
CREATE TRIGGER maintain_cyl_scan_latest_source_after_write
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
