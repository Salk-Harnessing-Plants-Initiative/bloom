-- 20260813120000_add_cyl_pipeline_dispatch_functions.sql
--
-- Phase 2 of bloom #11/#404 (the A4 pipeline-dispatch worker): adds
-- claim_cyl_pipeline_batch / complete_cyl_pipeline_batch /
-- fail_cyl_pipeline_batch, the three SECURITY DEFINER functions the new
-- dispatch worker (services/workflows/dispatch_worker.py) uses to drain
-- Phase 1's cyl_pipeline_dispatch queue and submit each batch to Argo as a
-- Kubernetes Workflow CRD. Modeled on bloom PR #469's (unmerged)
-- claim_cyl_video_job/complete_cyl_video_job/fail_cyl_video_job, adapted to
-- this queue's message shape ({run_id, batch_index, scan_ids} instead of
-- {job_id, scan_id, experiment_id}).
--
-- No table/column changes and NO bloom_workflows UPDATE grant — all three
-- functions are SECURITY DEFINER, so they write cyl_pipeline_runs/
-- cyl_pipeline_run_scans under the function owner's privileges, not the
-- caller's. See openspec/changes/add-cyl-pipeline-dispatch/design.md's "no
-- direct UPDATE grant" decision.
--
-- Unlike PR #469's cyl_video_jobs (which has its own 'processing' status to
-- guard against a late/duplicate complete-or-fail call), cyl_pipeline_run_scans
-- has no such state — a scan's status only ever moves to 'failed' (this phase)
-- or later to 'predicted'/'written'/'reused' (a different phase). The
-- equivalent settle-guard here is argo_workflow_name IS NULL: complete() only
-- ever sets it once (a second identical call is a no-op), and fail() never
-- touches a scan complete() already recorded — so a late/duplicate call in
-- either direction can't clobber a real outcome.
--
-- Run-completion aggregation (cyl_pipeline_runs.status) happens in a shared
-- private helper, _settle_cyl_pipeline_run, called by complete/fail and by
-- claim's own poison-message dead-letter branch (via a PERFORM of fail()) —
-- so a run whose last batch is dead-lettered by claim itself still settles,
-- not just runs settled by an explicit complete/fail call. The helper locks
-- the run row (SELECT ... FOR UPDATE) before checking/updating it, so two
-- workers settling the last two batches of the same run at once are
-- serialized rather than racing a read-then-write.
--
-- Forward-only + additive; companion manual rollback under supabase/rollbacks/.

BEGIN;

-- Private helper: not part of the public API, EXECUTE revoked from
-- PUBLIC/anon/authenticated and not granted to bloom_workflows either —
-- callers never invoke it directly, and a SECURITY DEFINER function's owner
-- always has implicit EXECUTE on functions it owns, so complete/fail/claim
-- (owned by the same role) can PERFORM it without a grant.
CREATE OR REPLACE FUNCTION public._settle_cyl_pipeline_run(p_run_id BIGINT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    -- Lock the run row first so two concurrent completers/failers of the same
    -- run are serialized: the second to acquire this lock is guaranteed to see
    -- the first's committed scan-row changes before it evaluates NOT EXISTS
    -- below, closing the race a plain read-then-write from Python could lose.
    PERFORM 1 FROM public.cyl_pipeline_runs WHERE id = p_run_id FOR UPDATE;

    UPDATE public.cyl_pipeline_runs
    SET status = CASE
            WHEN NOT EXISTS (
                SELECT 1 FROM public.cyl_pipeline_run_scans
                WHERE run_id = p_run_id AND status = 'failed'
            ) THEN 'submitted'
            WHEN NOT EXISTS (
                SELECT 1 FROM public.cyl_pipeline_run_scans
                WHERE run_id = p_run_id AND argo_workflow_name IS NOT NULL
            ) THEN 'failed'
            ELSE 'partial'
        END,
        completed_at = now()
    WHERE id = p_run_id
      AND NOT EXISTS (
          -- Only settle once every scan in the run has a terminal outcome
          -- (submitted, or failed) — otherwise there's a batch still
          -- outstanding and the run stays as-is.
          SELECT 1 FROM public.cyl_pipeline_run_scans
          WHERE run_id = p_run_id
            AND argo_workflow_name IS NULL
            AND status != 'failed'
      );
END;
$$;

REVOKE EXECUTE ON FUNCTION public._settle_cyl_pipeline_run(BIGINT)
    FROM PUBLIC, anon, authenticated;

-- fail: mark every scan in the batch failed (unless already successfully
-- submitted by a complete() call), increment attempts, dead-letter the
-- message, and settle the run. Defined before claim() below so claim's
-- poison-message branch can delegate to it.
CREATE OR REPLACE FUNCTION public.fail_cyl_pipeline_batch(
    p_run_id BIGINT,
    p_batch_index INTEGER,
    p_msg_id BIGINT,
    p_scan_ids BIGINT[],
    p_error TEXT
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
BEGIN
    UPDATE public.cyl_pipeline_run_scans
    SET status = 'failed',
        error_message = p_error,
        attempts = attempts + 1,
        updated_at = now()
    WHERE run_id = p_run_id
      AND scan_id = ANY(p_scan_ids)
      AND argo_workflow_name IS NULL;

    PERFORM pgmq.archive('cyl_pipeline_dispatch', p_msg_id);

    PERFORM public._settle_cyl_pipeline_run(p_run_id);
END;
$$;

-- claim: hand the worker the next batch — read one message (hidden for p_vt
-- seconds so no other worker claims it), dead-lettering it (via fail(), same
-- outcome an explicit fail call produces) instead of returning it if it has
-- been redelivered more than p_max_reads times, and returning nothing if the
-- queue is empty.
CREATE OR REPLACE FUNCTION public.claim_cyl_pipeline_batch(
    p_vt INTEGER DEFAULT 60,
    p_max_reads INTEGER DEFAULT 5
) RETURNS TABLE(run_id BIGINT, batch_index INTEGER, scan_ids BIGINT[], msg_id BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
    r pgmq.message_record;
    v_run_id BIGINT;
    v_batch_index INTEGER;
    v_scan_ids BIGINT[];
BEGIN
    SELECT * INTO r FROM pgmq.read('cyl_pipeline_dispatch', p_vt, 1) LIMIT 1;
    IF NOT FOUND THEN
        RETURN;  -- empty queue
    END IF;

    v_run_id := (r.message->>'run_id')::BIGINT;
    v_batch_index := (r.message->>'batch_index')::INTEGER;
    SELECT array_agg(value::BIGINT) INTO v_scan_ids
        FROM jsonb_array_elements_text(r.message->'scan_ids') AS value;

    -- Poison-message guard: every prior claimant crashed before ever calling
    -- complete/fail. Dead-letter by delegating to fail_cyl_pipeline_batch,
    -- which archives the message, marks the batch's scans failed, and settles
    -- the run — the same outcome an explicit fail call produces.
    IF r.read_ct > p_max_reads THEN
        PERFORM public.fail_cyl_pipeline_batch(
            v_run_id, v_batch_index, r.msg_id, v_scan_ids,
            format('dead-lettered after %s deliveries (poison message)', r.read_ct)
        );
        RETURN;
    END IF;

    RETURN QUERY SELECT v_run_id, v_batch_index, v_scan_ids, r.msg_id;
END;
$$;

-- complete: record the submitted workflow's name on every scan in the batch
-- (idempotent — a scan already recorded is left alone), delete the message,
-- and settle the run.
CREATE OR REPLACE FUNCTION public.complete_cyl_pipeline_batch(
    p_run_id BIGINT,
    p_batch_index INTEGER,
    p_msg_id BIGINT,
    p_scan_ids BIGINT[],
    p_argo_workflow_name TEXT
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
BEGIN
    UPDATE public.cyl_pipeline_run_scans
    SET argo_workflow_name = p_argo_workflow_name,
        updated_at = now()
    WHERE run_id = p_run_id
      AND scan_id = ANY(p_scan_ids)
      AND argo_workflow_name IS NULL;

    PERFORM pgmq.delete('cyl_pipeline_dispatch', p_msg_id);

    PERFORM public._settle_cyl_pipeline_run(p_run_id);
END;
$$;

-- Lock EXECUTE to bloom_workflows only — same triple-revoke rationale as
-- Phase 1's enqueue_cyl_pipeline_batch (Supabase grants EXECUTE to PUBLIC and
-- anon/authenticated on new public-schema functions by default).
REVOKE EXECUTE ON FUNCTION public.claim_cyl_pipeline_batch(INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.complete_cyl_pipeline_batch(BIGINT, INTEGER, BIGINT, BIGINT[], TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.fail_cyl_pipeline_batch(BIGINT, INTEGER, BIGINT, BIGINT[], TEXT)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.claim_cyl_pipeline_batch(INTEGER, INTEGER)
    TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.complete_cyl_pipeline_batch(BIGINT, INTEGER, BIGINT, BIGINT[], TEXT)
    TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.fail_cyl_pipeline_batch(BIGINT, INTEGER, BIGINT, BIGINT[], TEXT)
    TO bloom_workflows;

COMMIT;
