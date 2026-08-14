-- Manual rollback for 20260814010000_create_cyl_scan_latest_source.sql
-- Restores the live-WindowAgg view definition (verbatim from
-- 20260701000000_cyl_trait_read_source_aware.sql), then drops the trigger, function, and table,
-- in that order.

BEGIN;

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
