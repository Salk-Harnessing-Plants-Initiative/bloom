-- Rollback for 20260929010704_add_rnaseq_run_status_function.sql (run by hand).
-- Drops the status poller's function and puts back the step check without stage-reference;
-- runs keep the statuses already written.

BEGIN;

DROP FUNCTION IF EXISTS public.update_rnaseq_run_status(BIGINT, TEXT, TEXT, JSONB, INTEGER, TEXT);

-- Stops if a run still reports stage-reference, which the earlier check does not allow.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM public.rnaseq_runs WHERE current_step = 'stage-reference') THEN
    RAISE EXCEPTION 'runs still report the stage-reference step; clear them before rolling back';
  END IF;
END
$$;

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_current_step_check CHECK (
    current_step IS NULL
    OR (workflow_type = 'scrna-cellranger'
        AND current_step IN ('stage', 'qc', 'count', 'cleanup'))
);

COMMIT;
