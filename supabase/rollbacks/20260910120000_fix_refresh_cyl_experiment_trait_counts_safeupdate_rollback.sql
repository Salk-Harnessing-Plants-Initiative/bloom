-- Manual rollback for 20260910120000_fix_refresh_cyl_experiment_trait_counts_safeupdate.sql
--
-- *** WARNING: running this REINTRODUCES bloom#806. *** The restored body's unqualified DELETE
-- (line 21 below) is rejected by Postgres's safeupdate guard (session_preload_libraries on the
-- `authenticator` role) the moment anything calls this function over the real service_role/
-- PostgREST RPC path -- SQLSTATE 21000, "DELETE requires a WHERE clause". Only run this rollback
-- if you are deliberately reverting the bloom#806 fix itself (e.g. investigating a regression it
-- introduced); if you're rolling back for an unrelated reason, re-apply
-- 20260910120000_fix_refresh_cyl_experiment_trait_counts_safeupdate.sql immediately afterward.
--
-- Restores the exact pre-fix function body (the unqualified DELETE from
-- 20260817140000_create_cyl_experiment_trait_counts.sql) via CREATE OR REPLACE FUNCTION, not DROP
-- -- dropping would lose the function's service_role-only grant state, which a rollback should
-- restore, not remove. No out-of-order guard is needed: nothing after 20260817140000 references
-- refresh_cyl_experiment_trait_counts() itself (only the table it operates on, in
-- 20260817150000, which has its own independent rollback ordering).

BEGIN;

CREATE OR REPLACE FUNCTION public.refresh_cyl_experiment_trait_counts()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(0, hashtext('refresh_cyl_experiment_trait_counts'));

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

COMMIT;
