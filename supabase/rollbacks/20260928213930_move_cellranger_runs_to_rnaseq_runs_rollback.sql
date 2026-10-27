-- Rollback for 20260928213930_move_cellranger_runs_to_rnaseq_runs.sql (run by hand).
-- Puts back scrna_cellranger_runs, its scrna_cellranger_dispatch queue and the Cell Ranger
-- functions. Refuses if rnaseq_runs holds runs of any other workflow type.

BEGIN;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM public.rnaseq_runs WHERE workflow_type <> 'scrna-cellranger') THEN
    RAISE EXCEPTION 'rnaseq_runs has runs that are not Cell Ranger; they cannot be rolled back';
  END IF;
END
$$;

DROP FUNCTION IF EXISTS public.claim_rnaseq_run(INTEGER, INTEGER);
DROP FUNCTION IF EXISTS public.complete_rnaseq_run(BIGINT, BIGINT, TEXT);
DROP FUNCTION IF EXISTS public.fail_rnaseq_run(BIGINT, BIGINT, TEXT);
DROP FUNCTION IF EXISTS public._check_rnaseq_message(BIGINT, BIGINT);

-- Table
DROP INDEX IF EXISTS public.rnaseq_runs_workflow_type_status_idx;
DROP INDEX IF EXISTS public.rnaseq_runs_created_at_idx;
ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_scrna_cellranger_check;
ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_workflow_type_check;

ALTER TABLE public.rnaseq_runs ADD COLUMN sample TEXT;
ALTER TABLE public.rnaseq_runs ADD COLUMN reference TEXT;
UPDATE public.rnaseq_runs SET sample = params ->> 'sample', reference = params ->> 'reference';
ALTER TABLE public.rnaseq_runs ALTER COLUMN sample SET NOT NULL;
ALTER TABLE public.rnaseq_runs ALTER COLUMN reference SET NOT NULL;
ALTER TABLE public.rnaseq_runs DROP COLUMN workflow_type;
ALTER TABLE public.rnaseq_runs DROP COLUMN params;

ALTER TABLE public.rnaseq_runs RENAME TO scrna_cellranger_runs;
ALTER INDEX public.rnaseq_runs_pkey RENAME TO scrna_cellranger_runs_pkey;
ALTER SEQUENCE public.rnaseq_runs_id_seq RENAME TO scrna_cellranger_runs_id_seq;
ALTER TABLE public.scrna_cellranger_runs
    RENAME CONSTRAINT rnaseq_runs_status_check TO scrna_cellranger_runs_status_check;
ALTER POLICY admin_all_rnaseq_runs ON public.scrna_cellranger_runs RENAME TO admin_all_scrna_cellranger_runs;
ALTER POLICY agent_read_rnaseq_runs ON public.scrna_cellranger_runs RENAME TO agent_read_scrna_cellranger_runs;
ALTER POLICY user_read_rnaseq_runs ON public.scrna_cellranger_runs RENAME TO user_read_scrna_cellranger_runs;
ALTER POLICY workflows_read_rnaseq_runs ON public.scrna_cellranger_runs RENAME TO workflows_read_scrna_cellranger_runs;

ALTER TABLE public.scrna_cellranger_runs ADD CONSTRAINT scrna_cellranger_runs_sample_check
    CHECK (sample ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$' AND sample !~ '__');
ALTER TABLE public.scrna_cellranger_runs ADD CONSTRAINT scrna_cellranger_runs_reference_check
    CHECK (reference ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$' AND reference !~ '__');
ALTER TABLE public.scrna_cellranger_runs ADD CONSTRAINT scrna_cellranger_runs_current_step_check
    CHECK (current_step IS NULL OR current_step IN ('stage', 'qc', 'count', 'cleanup'));
ALTER TABLE public.scrna_cellranger_runs ADD CONSTRAINT scrna_cellranger_runs_run_key_check
    CHECK (run_key = sample || '__' || reference || '__' || requested_by::text);

-- Queue: waiting messages go back in their original order.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'scrna_cellranger_dispatch') THEN
    PERFORM pgmq.create('scrna_cellranger_dispatch');
  END IF;
  IF EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'rnaseq_dispatch') THEN
    PERFORM pgmq.send('scrna_cellranger_dispatch', q.message)
      FROM pgmq.q_rnaseq_dispatch q
      ORDER BY q.msg_id;
    PERFORM pgmq.drop_queue('rnaseq_dispatch');
  END IF;
END
$$;

-- Functions, as 20260928185707 and 20260928203335 created them.
-- Creates a queued run and its dispatch message in one transaction; returns the run id.
CREATE OR REPLACE FUNCTION public.request_scrna_cellranger_run(
    p_sample TEXT,
    p_reference TEXT,
    p_requested_by UUID
) RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
    v_name_rule CONSTANT TEXT := '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$';
    v_run_id BIGINT;
BEGIN
    IF p_sample IS NULL OR p_sample !~ v_name_rule OR p_sample ~ '__' THEN
        RAISE EXCEPTION 'invalid sample name: %', p_sample USING ERRCODE = '22023';
    END IF;
    IF p_reference IS NULL OR p_reference !~ v_name_rule OR p_reference ~ '__' THEN
        RAISE EXCEPTION 'invalid reference name: %', p_reference USING ERRCODE = '22023';
    END IF;
    IF p_requested_by IS NULL THEN
        RAISE EXCEPTION 'requested_by is required' USING ERRCODE = '22023';
    END IF;

    INSERT INTO scrna_cellranger_runs (sample, reference, run_key, requested_by)
    VALUES (p_sample, p_reference, p_sample || '__' || p_reference || '__' || p_requested_by::text, p_requested_by)
    RETURNING id INTO v_run_id;

    PERFORM pgmq.send('scrna_cellranger_dispatch', jsonb_build_object('run_id', v_run_id));

    RETURN v_run_id;
END;
$$;

-- Only bloom_workflows may create runs.
REVOKE EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID)
    TO bloom_workflows;

-- Marks a still-queued run failed with a message, and archives its queue message.
CREATE OR REPLACE FUNCTION public.fail_scrna_cellranger_run(
    p_run_id BIGINT,
    p_msg_id BIGINT,
    p_message TEXT
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
BEGIN
    UPDATE public.scrna_cellranger_runs
    SET status = 'failed',
        message = p_message,
        completed_at = now(),
        updated_at = now()
    WHERE id = p_run_id
      AND status = 'queued';

    PERFORM pgmq.archive('scrna_cellranger_dispatch', p_msg_id);
END;
$$;

-- Returns the next queued run and hides its message for p_vt seconds; returns no row
-- when the queue is empty or the message was dropped (stale, malformed or redelivered
-- more than p_max_reads times).
CREATE OR REPLACE FUNCTION public.claim_scrna_cellranger_run(
    p_vt INTEGER DEFAULT 60,
    p_max_reads INTEGER DEFAULT 5
) RETURNS TABLE(run_id BIGINT, sample TEXT, reference TEXT, run_key TEXT, msg_id BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
    m pgmq.message_record;
    v_run_id BIGINT;
    v_run public.scrna_cellranger_runs%ROWTYPE;
BEGIN
    SELECT * INTO m FROM pgmq.read('scrna_cellranger_dispatch', p_vt, 1) LIMIT 1;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    -- A message without a numeric run_id can never be processed.
    IF (m.message->>'run_id') IS NULL OR (m.message->>'run_id') !~ '^[0-9]+$' THEN
        PERFORM pgmq.archive('scrna_cellranger_dispatch', m.msg_id);
        RETURN;
    END IF;
    v_run_id := (m.message->>'run_id')::BIGINT;

    SELECT * INTO v_run FROM public.scrna_cellranger_runs r WHERE r.id = v_run_id;

    -- The run is gone or already past queued: nothing left to submit.
    IF NOT FOUND OR v_run.status <> 'queued' THEN
        PERFORM pgmq.delete('scrna_cellranger_dispatch', m.msg_id);
        RETURN;
    END IF;

    IF m.read_ct > p_max_reads THEN
        PERFORM public.fail_scrna_cellranger_run(
            v_run_id, m.msg_id,
            format('not submitted after %s attempts', m.read_ct - 1)
        );
        RETURN;
    END IF;

    RETURN QUERY SELECT v_run.id, v_run.sample, v_run.reference, v_run.run_key, m.msg_id;
END;
$$;

-- Records a still-queued run's submitted workflow and deletes its queue message.
CREATE OR REPLACE FUNCTION public.complete_scrna_cellranger_run(
    p_run_id BIGINT,
    p_msg_id BIGINT,
    p_argo_workflow_name TEXT
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
BEGIN
    IF p_argo_workflow_name IS NULL OR btrim(p_argo_workflow_name) = '' THEN
        RAISE EXCEPTION 'argo workflow name is required' USING ERRCODE = '22023';
    END IF;

    UPDATE public.scrna_cellranger_runs
    SET status = 'submitted',
        argo_workflow_name = p_argo_workflow_name,
        submitted_at = now(),
        updated_at = now()
    WHERE id = p_run_id
      AND status = 'queued';

    PERFORM pgmq.delete('scrna_cellranger_dispatch', p_msg_id);
END;
$$;

-- Only bloom_workflows may call them.
REVOKE EXECUTE ON FUNCTION public.claim_scrna_cellranger_run(INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.complete_scrna_cellranger_run(BIGINT, BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.fail_scrna_cellranger_run(BIGINT, BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.claim_scrna_cellranger_run(INTEGER, INTEGER)
    TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.complete_scrna_cellranger_run(BIGINT, BIGINT, TEXT)
    TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.fail_scrna_cellranger_run(BIGINT, BIGINT, TEXT)
    TO bloom_workflows;

COMMIT;
