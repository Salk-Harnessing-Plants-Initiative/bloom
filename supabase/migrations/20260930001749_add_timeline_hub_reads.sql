-- 20260930001749_add_timeline_hub_reads.sql
--
-- Two reads for the Timeline page's panels:
--   gravi_scan_timeline: plate scan batches per day, species, experiment and wave, the
--     plate counterpart of cyl_wave_timeline. security_invoker, so each reader sees only
--     the plate scans their own policies allow.
--   rnaseq_run_requesters(p_run_ids): the email of whoever started each given RNA-seq
--     run. Scientists can read rnaseq_runs but not auth.users, so the run view shows
--     "Started by" through this; it reveals only the starters of runs it is asked about.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- 1. Plate scan batches ------------------------------------------------------------

CREATE OR REPLACE VIEW public.gravi_scan_timeline
WITH (security_invoker = true) AS
SELECT
    (s.capture_date AT TIME ZONE 'UTC')::date AS date_scanned,
    sp.common_name AS species_name,
    e.name AS experiment_name,
    s.wave_number,
    count(*) AS count
FROM public.gravi_scans s
JOIN public.gravi_experiments e ON e.id = s.experiment_id
LEFT JOIN public.species sp ON sp.id = e.species_id
GROUP BY 1, 2, 3, 4
ORDER BY 1 DESC;

-- The roles that read gravi_scans today.
REVOKE ALL ON public.gravi_scan_timeline FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.gravi_scan_timeline
    TO bloom_user, bloom_writer, bloom_admin, bloom_agent, bloom_workflows;

-- 2. Who started a run ------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.rnaseq_run_requesters(p_run_ids BIGINT[])
RETURNS TABLE (run_id BIGINT, email TEXT)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT r.id, u.email::text
    FROM public.rnaseq_runs r
    JOIN auth.users u ON u.id = r.requested_by
    WHERE r.id = ANY (p_run_ids);
$$;

-- Signed-in scientists, writers and admins; not anon, and not the plain authenticated role.
REVOKE ALL ON FUNCTION public.rnaseq_run_requesters(BIGINT[]) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.rnaseq_run_requesters(BIGINT[])
    TO bloom_user, bloom_writer, bloom_admin;

COMMIT;
