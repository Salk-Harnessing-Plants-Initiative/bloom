-- Manual rollback for 20260814010000_create_cyl_scan_latest_source.sql
-- Restores the live-WindowAgg view definition (verbatim from
-- 20260701000000_cyl_trait_read_source_aware.sql), then drops the trigger, function, and table,
-- in that order.
--
-- *** ROLLBACK ORDER: apply 20260814030000's rollback, then 20260814020000's, THEN this one. ***
-- refresh_cyl_experiment_trait_counts() (20260814020000) reads cyl_scan_latest_source in its own
-- PL/pgSQL body -- a reference Postgres's dependency tracker does NOT protect (unlike this
-- migration's view, which pg_depend DOES protect against DROP). Running this rollback while that
-- function still exists does not fail loudly at DROP time; it fails later, at the next scheduled
-- refresh, with "relation cyl_scan_latest_source does not exist". The guard below only checks for
-- that one function; it does not know about 20260814030000's dependencies transitively, so the
-- full reverse-chronological order still matters even though the guard makes the 020000 case safe.

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_proc
        WHERE proname = 'refresh_cyl_experiment_trait_counts'
          AND pronamespace = 'public'::regnamespace
    ) THEN
        RAISE EXCEPTION 'Roll back 20260814020000 (and 20260814030000, if not already done) before this one -- refresh_cyl_experiment_trait_counts() still references cyl_scan_latest_source.';
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
