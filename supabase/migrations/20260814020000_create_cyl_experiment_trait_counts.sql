-- bloom#637 / bloom#656 (supersedes PR #654's rollup table): cache n_traits per experiment,
-- refreshed on a schedule rather than a per-write trigger.
--
-- n_plants needs no cache at all (see 20260814030000's get_experiment_summary_counts rewrite --
-- a live EXISTS semi-join is 247ms for every experiment, per @blm3886's bloom#656 measurement).
-- n_traits genuinely needs caching (a full scan, 6.6s, no shortcut available) -- but PR #654's
-- per-row AFTER trigger is the wrong refresh shape: one write-back upload inserts on the order of
-- hundreds of trait rows in a loop, so a per-row trigger would fire that many full-experiment
-- recomputes for one upload. This table is refreshed by an external schedule instead (see
-- openspec/changes/fix-cyl-scan-traits-latest-rollup/tasks.md section 5 for the scheduling job) --
-- refresh_cyl_experiment_trait_counts() does one full, unconditional rebuild per call, so its cost
-- is fixed regardless of ingest volume.
--
-- Manual rollback: supabase/rollbacks/20260814020000_create_cyl_experiment_trait_counts_rollback.sql

BEGIN;

CREATE TABLE IF NOT EXISTS public.cyl_experiment_trait_counts (
    experiment_id bigint PRIMARY KEY REFERENCES public.cyl_experiments(id) ON DELETE CASCADE,
    n_traits      int NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.cyl_experiment_trait_counts ENABLE ROW LEVEL SECURITY;

-- Matches cyl_scan_traits's own policy set exactly (20260506000001_bloom_role_rls_policies.sql +
-- its original 20231113203010 creation migration) -- permissive USING (true) for the same four
-- read roles this table's own GRANT below lists; this table holds only aggregate counts already
-- derivable by those roles from cyl_scan_traits itself.
DROP POLICY IF EXISTS admin_all_cyl_experiment_trait_counts ON public.cyl_experiment_trait_counts;
CREATE POLICY admin_all_cyl_experiment_trait_counts ON public.cyl_experiment_trait_counts
    FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_cyl_experiment_trait_counts ON public.cyl_experiment_trait_counts;
CREATE POLICY agent_read_cyl_experiment_trait_counts ON public.cyl_experiment_trait_counts
    FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_cyl_experiment_trait_counts ON public.cyl_experiment_trait_counts;
CREATE POLICY user_read_cyl_experiment_trait_counts ON public.cyl_experiment_trait_counts
    FOR SELECT TO bloom_user USING (true);
DROP POLICY IF EXISTS authenticated_read_cyl_experiment_trait_counts ON public.cyl_experiment_trait_counts;
CREATE POLICY authenticated_read_cyl_experiment_trait_counts ON public.cyl_experiment_trait_counts
    FOR SELECT TO authenticated USING (true);

-- This table backs get_experiment_summary_counts (SECURITY INVOKER), so a caller needs SELECT
-- here directly, same reasoning as cyl_scan_latest_source in 20260814010000. Granted alongside
-- the refresh function's own, deliberately narrower grant below.
GRANT SELECT ON public.cyl_experiment_trait_counts
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

CREATE OR REPLACE FUNCTION public.refresh_cyl_experiment_trait_counts()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    DELETE FROM public.cyl_experiment_trait_counts;
    INSERT INTO public.cyl_experiment_trait_counts (experiment_id, n_traits, updated_at)
    SELECT d.experiment_id, count(*), now()
    FROM (
        SELECT DISTINCT w.experiment_id, cst.trait_id
        FROM public.cyl_waves              w
        JOIN public.cyl_plants             p   ON p.wave_id = w.id AND p.accession_id IS NOT NULL
        JOIN public.cyl_scans              s   ON s.plant_id = p.id
        JOIN public.cyl_scan_traits        cst ON cst.scan_id = s.id
        JOIN public.cyl_scan_latest_source l   ON l.scan_id = cst.scan_id
            AND cst.source_id IS NOT DISTINCT FROM l.max_source_id
        WHERE cst.trait_id IS NOT NULL
    ) d
    GROUP BY d.experiment_id;
END;
$$;

-- Maintenance job only, not a user-facing call -- deliberately not granted to the four read
-- roles the way read RPCs are (design.md D5). Supabase's default privileges auto-grant EXECUTE
-- on new public-schema functions to anon/authenticated/service_role, so those must be revoked
-- explicitly too, not just PUBLIC.
REVOKE EXECUTE ON FUNCTION public.refresh_cyl_experiment_trait_counts() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.refresh_cyl_experiment_trait_counts() TO service_role;

-- One-time initial population so the cache isn't empty until the first scheduled run fires.
SELECT public.refresh_cyl_experiment_trait_counts();

COMMIT;
