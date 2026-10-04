-- Rollback for 20261004120000_add_cyl_pipeline_run_workflows.sql (bloom#1042)
-- Manual break-glass only.
--
-- FIRST redeploy a status poller that doesn't use these objects (any build before
-- fix-cyl-poller-unconcluded-runs PR B). The PR B poller selects poller_concluded_at for its
-- candidate runs and reads cyl_pipeline_run_workflows; with this rollback applied under it, every
-- sweep fails.
--
-- Restores update_cyl_pipeline_run_status to its 20260912111000 body: 'partial' is again a
-- source status, so a concluded run can be re-polled and re-stamped (bloom#1042 item 3). Then
-- drops both new functions, the table (and the phases it holds) and poller_concluded_at.

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE OR REPLACE FUNCTION public.update_cyl_pipeline_run_status(
    p_run_id BIGINT,
    p_status TEXT,
    p_done_count INTEGER DEFAULT NULL,
    p_failed_count INTEGER DEFAULT NULL
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF p_status NOT IN ('running', 'complete', 'failed', 'partial') THEN
        RAISE EXCEPTION
            'update_cyl_pipeline_run_status: invalid p_status %, must be one of '
            'running/complete/failed/partial', p_status;
    END IF;

    UPDATE public.cyl_pipeline_runs
    SET status = p_status,
        done_count = coalesce(p_done_count, done_count),
        failed_count = coalesce(p_failed_count, failed_count),
        completed_at = CASE
            WHEN p_status IN ('complete', 'failed', 'partial')
            THEN now()
            ELSE completed_at
        END
    WHERE id = p_run_id
      AND status IN ('submitted', 'running', 'partial');
END;
$$;

REVOKE EXECUTE ON FUNCTION public.update_cyl_pipeline_run_status(BIGINT, TEXT, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.update_cyl_pipeline_run_status(BIGINT, TEXT, INTEGER, INTEGER)
    TO bloom_workflows;

DROP FUNCTION IF EXISTS public.close_cyl_pipeline_run_workflow_scans(BIGINT, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.record_cyl_pipeline_workflow_phase(BIGINT, TEXT, TEXT);
DROP TABLE IF EXISTS public.cyl_pipeline_run_workflows;
ALTER TABLE public.cyl_pipeline_runs DROP COLUMN IF EXISTS poller_concluded_at;

COMMIT;

NOTIFY pgrst, 'reload schema';
