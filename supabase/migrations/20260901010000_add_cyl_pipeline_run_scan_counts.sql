-- update_cyl_pipeline_run_status gains done_count/failed_count (bloom #716).
-- Change: fix-cyl-pipeline-run-scan-status. Companion to
-- 20260901000000_add_cyl_writeback_run_scan_status.sql (a separate migration
-- by design — this touches cyl_pipeline_runs and is consumed only by
-- status_poller.py, whereas the other touches cyl_pipeline_run_scans and is
-- consumed by bloomctl/Argo write-back pods; the two share no
-- cross-references and apply independently of each other's order, so
-- splitting them lets either be rolled back without the other — see
-- design.md Decision 5).
--
-- WHY: cyl_pipeline_runs.done_count/failed_count have existed since Phase 1
--   but nothing has ever populated them. status_poller.py's sweep_once now
--   computes them each cycle from cyl_pipeline_run_scans.status (which the
--   companion migration above makes meaningful for the first time) and needs
--   a way to write them alongside the existing status/completed_at write.
--
-- WHAT: p_done_count/p_failed_count, both INTEGER DEFAULT NULL. Supplying a
--   value SETs the corresponding column; omitting it (the default) leaves
--   the column unchanged via COALESCE, so any caller not yet updated to pass
--   them keeps working exactly as before. Everything else — the p_status
--   validation, the eligible-source-status WHERE clause, the
--   completed_at-on-terminal-transition logic — is unchanged from the
--   existing 2-arg function.
--
-- Signature change (adds two parameters), so DROP FUNCTION IF EXISTS on the
-- OLD 2-arg signature + CREATE OR REPLACE on the NEW 4-arg signature, same
-- rationale as the companion migration: avoids leaving the old overload
-- behind as dead, still-callable code, while staying idempotent when the
-- whole migration body is re-applied in one transaction (IF EXISTS on the
-- drop, OR REPLACE on the create).
--
-- No table/column changes. Forward-only.
-- Manual rollback: supabase/rollbacks/20260901010000_add_cyl_pipeline_run_scan_counts_rollback.sql

BEGIN;

DROP FUNCTION IF EXISTS public.update_cyl_pipeline_run_status(BIGINT, TEXT);

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

-- Lock EXECUTE to bloom_workflows only — same triple-revoke rationale as
-- every prior wrapper in this program (Supabase grants EXECUTE to PUBLIC and
-- anon/authenticated on new public-schema functions by default). A DROP
-- discards grants, so re-issue explicitly rather than assuming they carry
-- over.
REVOKE EXECUTE ON FUNCTION public.update_cyl_pipeline_run_status(BIGINT, TEXT, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.update_cyl_pipeline_run_status(BIGINT, TEXT, INTEGER, INTEGER)
    TO bloom_workflows;

COMMIT;
