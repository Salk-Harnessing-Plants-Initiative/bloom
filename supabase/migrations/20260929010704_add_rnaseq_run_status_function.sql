-- 20260929010704_add_rnaseq_run_status_function.sql
--
-- The status poller's one call: record what Argo reports for a submitted or running
-- RNA-seq run (its current step, the step pods for logs, and at the end the outcome,
-- exit code and message). Runs only move forward, and a finished run never changes.
-- Also adds Cell Ranger's stage-reference step to the steps a run can report.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- Cell Ranger's steps, in order; stage-reference prepares the reference before the sample's steps.
ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_current_step_check CHECK (
    current_step IS NULL
    OR (workflow_type = 'scrna-cellranger'
        AND current_step IN ('stage-reference', 'stage', 'qc', 'count', 'cleanup'))
);

-- Sets a submitted or running run's status, step and step pods, and on finishing its exit
-- code, message and completed_at. A NULL step or pods keeps the stored value. Returns
-- whether the row changed; an identical report writes nothing.
CREATE OR REPLACE FUNCTION public.update_rnaseq_run_status(
    p_run_id BIGINT,
    p_status TEXT,
    p_current_step TEXT,
    p_step_pods JSONB,
    p_exit_code INTEGER,
    p_message TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_finished BOOLEAN;
    v_changed INTEGER;
BEGIN
    IF p_run_id IS NULL THEN
        RAISE EXCEPTION 'run id is required' USING ERRCODE = '22023';
    END IF;
    IF p_status IS NULL OR p_status NOT IN ('running', 'succeeded', 'skipped', 'failed') THEN
        RAISE EXCEPTION 'status must be running, succeeded, skipped or failed, not %', p_status
            USING ERRCODE = '22023';
    END IF;
    IF p_status = 'failed' AND (p_message IS NULL OR btrim(p_message) = '') THEN
        RAISE EXCEPTION 'a failed run needs a message' USING ERRCODE = '22023';
    END IF;
    IF p_step_pods IS NOT NULL AND jsonb_typeof(p_step_pods) <> 'object' THEN
        RAISE EXCEPTION 'step pods must be a JSON object' USING ERRCODE = '22023';
    END IF;

    v_finished := p_status IN ('succeeded', 'skipped', 'failed');

    UPDATE public.rnaseq_runs r
    SET status = p_status,
        current_step = coalesce(p_current_step, r.current_step),
        step_pods = coalesce(p_step_pods, r.step_pods),
        exit_code = CASE WHEN v_finished THEN p_exit_code ELSE r.exit_code END,
        message = CASE WHEN v_finished THEN p_message ELSE r.message END,
        completed_at = CASE WHEN v_finished THEN now() ELSE r.completed_at END,
        updated_at = now()
    WHERE r.id = p_run_id
      AND r.status IN ('submitted', 'running')
      AND (
          r.status IS DISTINCT FROM p_status
          OR (p_current_step IS NOT NULL AND r.current_step IS DISTINCT FROM p_current_step)
          OR (p_step_pods IS NOT NULL AND r.step_pods IS DISTINCT FROM p_step_pods)
      );
    GET DIAGNOSTICS v_changed = ROW_COUNT;
    RETURN v_changed > 0;
END;
$$;

-- Only bloom_workflows may call it.
REVOKE EXECUTE ON FUNCTION public.update_rnaseq_run_status(BIGINT, TEXT, TEXT, JSONB, INTEGER, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.update_rnaseq_run_status(BIGINT, TEXT, TEXT, JSONB, INTEGER, TEXT)
    TO bloom_workflows;

COMMIT;
