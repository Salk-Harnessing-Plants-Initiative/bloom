-- 20260928203335_add_scrna_cellranger_dispatch_functions.sql
--
-- The dispatch worker's three calls for Cell Ranger runs: claim the next queued run,
-- record its submitted Argo workflow, or record why it could not be submitted.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

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
