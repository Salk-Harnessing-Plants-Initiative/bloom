-- Manual rollback for 20260817010000_create_cyl_scan_latest_source.sql
-- Restores the live-WindowAgg view definition (verbatim from
-- 20260701000000_cyl_trait_read_source_aware.sql), then drops the trigger, function, and table,
-- in that order.
--
-- *** ROLLBACK ORDER: apply 20260817030000's rollback, then 20260817020000's, THEN this one. ***
-- refresh_cyl_experiment_trait_counts() (20260817020000) reads cyl_scan_latest_source in its own
-- PL/pgSQL body -- a reference Postgres's dependency tracker does NOT protect (unlike this
-- migration's view, which pg_depend DOES protect against DROP). Running this rollback while that
-- function still exists does not fail loudly at DROP time; it fails later, at the next scheduled
-- refresh, with "relation cyl_scan_latest_source does not exist". The guard below does not know
-- about 20260817030000's dependencies transitively, so the full reverse-chronological order still
-- matters even though the guard makes the 020000 case safe.
--
-- The guard checks cyl_experiment_trait_counts (the TABLE), not just the refresh function's own
-- existence -- found in a later review round: checking only the function is a narrower, less
-- robust invariant. If the function were ever removed out-of-band (e.g. a manual
-- `DROP FUNCTION refresh_cyl_experiment_trait_counts()` without running 20260817020000's own
-- rollback), a function-only check would see "absent" and let this rollback proceed even though
-- 020000's table (and 030000's RPC, if not yet rolled back) still depend on cyl_scan_latest_source
-- transitively. The table is the more durable signal that 020000 hasn't actually been rolled back.

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_proc
        WHERE proname = 'refresh_cyl_experiment_trait_counts'
          AND pronamespace = 'public'::regnamespace
    ) OR EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname = 'public' AND tablename = 'cyl_experiment_trait_counts'
    ) THEN
        RAISE EXCEPTION 'Roll back 20260817020000 (and 20260817030000, if not already done) before this one -- cyl_experiment_trait_counts and/or refresh_cyl_experiment_trait_counts() still reference cyl_scan_latest_source.';
    END IF;
END;
$$;

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
    (cst.source_id IS NOT DISTINCT FROM
        max(cst.source_id) OVER (PARTITION BY cst.scan_id)) AS is_latest
FROM public.cyl_scan_traits cst
LEFT JOIN public.cyl_trait_sources s ON s.id = cst.source_id
LEFT JOIN public.cyl_traits       t ON t.id = cst.trait_id;

GRANT SELECT ON public.cyl_scan_traits_source
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

DROP TRIGGER IF EXISTS maintain_cyl_scan_latest_source_after_write ON public.cyl_scan_traits;
DROP FUNCTION IF EXISTS public.maintain_cyl_scan_latest_source();
DROP TABLE IF EXISTS public.cyl_scan_latest_source;

COMMIT;
