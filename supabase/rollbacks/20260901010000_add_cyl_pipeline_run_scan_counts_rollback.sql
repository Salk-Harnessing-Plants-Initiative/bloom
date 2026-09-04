-- Rollback for 20260901010000_add_cyl_pipeline_run_scan_counts.sql
-- Manual break-glass only. Restores update_cyl_pipeline_run_status to its
-- prior 2-arg signature and grant.
--
-- WARNING: rolling this back while a status_poller.py build calling the
-- 4-arg signature is still running will break every status-poll write —
-- Postgres RPC dispatch is signature-based. Roll back (or redeploy) the
-- `workflows`/`cyl-status-poller` container in lockstep, or prefer a
-- forward fix (re-applying a corrected version of the forward migration)
-- instead — see design.md's rollback-coupling risk.
--
-- Leaves cyl_pipeline_runs and every other function untouched — the forward
-- migration made no table/column changes.

BEGIN;

DROP FUNCTION IF EXISTS public.update_cyl_pipeline_run_status(BIGINT, TEXT, INTEGER, INTEGER);

CREATE OR REPLACE FUNCTION public.update_cyl_pipeline_run_status(
    p_run_id BIGINT,
    p_status TEXT
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
        completed_at = CASE
            WHEN p_status IN ('complete', 'failed', 'partial')
            THEN now()
            ELSE completed_at
        END
    WHERE id = p_run_id
      AND status IN ('submitted', 'running', 'partial');
END;
$$;

REVOKE EXECUTE ON FUNCTION public.update_cyl_pipeline_run_status(BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.update_cyl_pipeline_run_status(BIGINT, TEXT)
    TO bloom_workflows;

COMMIT;
