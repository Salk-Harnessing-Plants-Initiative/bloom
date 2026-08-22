-- 20260818130000_add_cyl_pipeline_run_status_polling.sql
--
-- Phase 3 of bloom #11 (status polling): adds update_cyl_pipeline_run_status,
-- the SECURITY DEFINER function a new standalone poller
-- (services/workflows/status_poller.py) calls after observing a run's real
-- Argo Workflow phase(s), to progress cyl_pipeline_runs.status past what
-- Phase 2's claim/complete/fail_cyl_pipeline_batch ever reach.
--
-- Phase 2 only ever resolves a run to 'submitted'/'failed'/'partial' —
-- dispatch outcome (did the K8s API accept the submission). 'running' and
-- pipeline-outcome 'complete'/'failed'/'partial' describe the real Argo
-- Workflow outcome once dispatched; nothing wrote them before this migration,
-- even though the CHECK constraint on cyl_pipeline_runs.status has allowed
-- all four values since Phase 1's own migration.
--
-- The rollup computation (which effective phases map to which run status)
-- happens in Python (the poller), not here — see
-- openspec/changes/add-cyl-pipeline-status-polling/design.md's "rollup
-- computation happens in Python" decision for why that's safe despite Phase
-- 2's own "aggregate in SQL" precedent. This function is a thin, guarded
-- writer: it validates the target status, and only ever updates a run
-- currently 'submitted', 'running', or 'partial' — a run still 'queued'
-- (never dispatched) or already fully terminal ('complete'/'failed') is
-- left untouched. 'partial' is included as a source state (not just a
-- target) because Phase 2's own dispatch-level 'partial' can still have
-- genuinely-dispatched batches whose real Argo outcome hasn't been checked
-- yet — see design.md's "'partial' runs are included in the polling
-- candidate set" decision (found during /review-pr round 1).
--
-- No table/column changes — the CHECK constraint already allows every value
-- this function writes. No new bloom_workflows table grant — same
-- SECURITY DEFINER convention every prior wrapper in this program uses.
--
-- Forward-only + additive; companion manual rollback under supabase/rollbacks/.

BEGIN;

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
        -- Stamp completed_at unconditionally on every real terminal
        -- conclusion, not only the first (found during /review-pr round 1:
        -- Phase 2's own dispatch-settle write already sets completed_at for
        -- the common 'submitted' case before this function is ever called,
        -- so a "first time only" guard here would never actually fire in
        -- production — it would silently freeze completed_at at dispatch
        -- time forever instead of reflecting when the pipeline itself
        -- finished). A 'partial' run reconfirmed as 'partial' advances
        -- completed_at again each time — an accepted, documented
        -- consequence of 'partial' remaining a pollable source state below,
        -- not a bug: see design.md.
        completed_at = CASE
            WHEN p_status IN ('complete', 'failed', 'partial')
            THEN now()
            ELSE completed_at
        END
    WHERE id = p_run_id
      -- A run still 'queued' was never dispatched — this function has
      -- nothing to say about it. A run already 'complete'/'failed' is fully
      -- terminal and must not be reopened by a stale/redelivered poll cycle.
      -- 'submitted' (Phase 2's dispatch-succeeded outcome), 'running' (this
      -- function's own prior write), and 'partial' (Phase 2's mixed dispatch
      -- outcome, which may still have real batches in flight) are all
      -- eligible.
      AND status IN ('submitted', 'running', 'partial');
END;
$$;

-- Lock EXECUTE to bloom_workflows only — same triple-revoke rationale as
-- every prior wrapper in this program (Supabase grants EXECUTE to PUBLIC and
-- anon/authenticated on new public-schema functions by default).
REVOKE EXECUTE ON FUNCTION public.update_cyl_pipeline_run_status(BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.update_cyl_pipeline_run_status(BIGINT, TEXT)
    TO bloom_workflows;

COMMIT;
