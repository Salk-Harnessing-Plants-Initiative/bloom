-- 20260928213930_move_cellranger_runs_to_rnaseq_runs.sql
--
-- One runs table and one queue for every RNA-seq workflow type. Cell Ranger runs become
-- rnaseq_runs rows with workflow_type 'scrna-cellranger' and their inputs in params; the
-- dispatch worker's claim, complete and fail calls become common to all types.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- 1. rnaseq_runs --------------------------------------------------------------

DO $$
BEGIN
  IF to_regclass('public.rnaseq_runs') IS NULL
     AND to_regclass('public.scrna_cellranger_runs') IS NOT NULL THEN
    ALTER TABLE public.scrna_cellranger_runs RENAME TO rnaseq_runs;
  END IF;
END
$$;

ALTER INDEX IF EXISTS public.scrna_cellranger_runs_pkey RENAME TO rnaseq_runs_pkey;
ALTER SEQUENCE IF EXISTS public.scrna_cellranger_runs_id_seq RENAME TO rnaseq_runs_id_seq;

DO $$
DECLARE
  v_role TEXT;
BEGIN
  FOREACH v_role IN ARRAY ARRAY['admin_all', 'agent_read', 'user_read', 'workflows_read'] LOOP
    IF EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname = 'public' AND tablename = 'rnaseq_runs'
        AND policyname = v_role || '_scrna_cellranger_runs'
    ) THEN
      EXECUTE format(
        'ALTER POLICY %I ON public.rnaseq_runs RENAME TO %I',
        v_role || '_scrna_cellranger_runs', v_role || '_rnaseq_runs'
      );
    END IF;
  END LOOP;

  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.rnaseq_runs'::regclass
      AND conname = 'scrna_cellranger_runs_status_check'
  ) THEN
    ALTER TABLE public.rnaseq_runs
      RENAME CONSTRAINT scrna_cellranger_runs_status_check TO rnaseq_runs_status_check;
  END IF;
END
$$;

-- Which workflow ran; the UI groups runs by it.
ALTER TABLE public.rnaseq_runs ADD COLUMN IF NOT EXISTS workflow_type TEXT;
-- The workflow type's inputs; for Cell Ranger, {"sample": ..., "reference": ...}.
ALTER TABLE public.rnaseq_runs ADD COLUMN IF NOT EXISTS params JSONB;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'rnaseq_runs' AND column_name = 'sample'
  ) THEN
    EXECUTE $sql$
      UPDATE public.rnaseq_runs
      SET workflow_type = 'scrna-cellranger',
          params = jsonb_build_object('sample', sample, 'reference', reference)
      WHERE params IS NULL
    $sql$;
  END IF;
END
$$;

ALTER TABLE public.rnaseq_runs ALTER COLUMN workflow_type SET NOT NULL;
ALTER TABLE public.rnaseq_runs ALTER COLUMN params SET NOT NULL;

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS scrna_cellranger_runs_run_key_check;
ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS scrna_cellranger_runs_current_step_check;
ALTER TABLE public.rnaseq_runs DROP COLUMN IF EXISTS sample;
ALTER TABLE public.rnaseq_runs DROP COLUMN IF EXISTS reference;

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_workflow_type_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_workflow_type_check
    CHECK (workflow_type IN ('scrna-cellranger'));

-- Cell Ranger inputs: exactly a safe sample and reference name, and the run_key built from them.
ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_scrna_cellranger_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_scrna_cellranger_check CHECK (
    workflow_type <> 'scrna-cellranger' OR coalesce(
        jsonb_typeof(params) = 'object'
        AND params - 'sample' - 'reference' = '{}'::jsonb
        AND jsonb_typeof(params -> 'sample') = 'string'
        AND jsonb_typeof(params -> 'reference') = 'string'
        AND params ->> 'sample' ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'
        AND params ->> 'sample' !~ '__'
        AND params ->> 'reference' ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'
        AND params ->> 'reference' !~ '__'
        AND run_key = (params ->> 'sample') || '__' || (params ->> 'reference')
                      || '__' || requested_by::text,
        false
    )
);

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_current_step_check CHECK (
    current_step IS NULL
    OR (workflow_type = 'scrna-cellranger'
        AND current_step IN ('stage', 'qc', 'count', 'cleanup'))
);

CREATE INDEX IF NOT EXISTS rnaseq_runs_workflow_type_status_idx
    ON public.rnaseq_runs (workflow_type, status);
CREATE INDEX IF NOT EXISTS rnaseq_runs_created_at_idx
    ON public.rnaseq_runs (created_at DESC);

-- Same access as before: read for bloom_user, bloom_agent and bloom_workflows; full for bloom_admin.
REVOKE ALL ON public.rnaseq_runs
    FROM anon, authenticated, bloom_user, bloom_agent, bloom_writer, bloom_workflows;
REVOKE UPDATE, DELETE, TRUNCATE ON public.rnaseq_runs FROM service_role;
GRANT SELECT ON public.rnaseq_runs TO bloom_user, bloom_agent, bloom_workflows;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.rnaseq_runs TO bloom_admin;

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE public.rnaseq_runs;
EXCEPTION WHEN duplicate_object THEN
  NULL;
END$$;

-- 2. rnaseq_dispatch ----------------------------------------------------------

DROP FUNCTION IF EXISTS public.claim_scrna_cellranger_run(INTEGER, INTEGER);
DROP FUNCTION IF EXISTS public.complete_scrna_cellranger_run(BIGINT, BIGINT, TEXT);
DROP FUNCTION IF EXISTS public.fail_scrna_cellranger_run(BIGINT, BIGINT, TEXT);

-- Waiting Cell Ranger messages move to the shared queue in their original order.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'rnaseq_dispatch') THEN
    PERFORM pgmq.create('rnaseq_dispatch');
  END IF;
  IF EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'scrna_cellranger_dispatch') THEN
    PERFORM pgmq.send('rnaseq_dispatch', q.message)
      FROM pgmq.q_scrna_cellranger_dispatch q
      ORDER BY q.msg_id;
    PERFORM pgmq.drop_queue('scrna_cellranger_dispatch');
  END IF;
END
$$;

-- 3. Functions ----------------------------------------------------------------

-- Creates a queued Cell Ranger run and its dispatch message in one transaction; returns the run id.
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

    INSERT INTO public.rnaseq_runs (workflow_type, params, run_key, requested_by)
    VALUES (
        'scrna-cellranger',
        jsonb_build_object('sample', p_sample, 'reference', p_reference),
        p_sample || '__' || p_reference || '__' || p_requested_by::text,
        p_requested_by
    )
    RETURNING id INTO v_run_id;

    PERFORM pgmq.send('rnaseq_dispatch', jsonb_build_object('run_id', v_run_id));

    RETURN v_run_id;
END;
$$;

-- Raises if p_msg_id is a queued message for a run other than p_run_id.
CREATE OR REPLACE FUNCTION public._check_rnaseq_message(p_run_id BIGINT, p_msg_id BIGINT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
    v_owner TEXT;
BEGIN
    IF p_run_id IS NULL OR p_msg_id IS NULL THEN
        RAISE EXCEPTION 'run id and message id are required' USING ERRCODE = '22023';
    END IF;
    SELECT q.message ->> 'run_id' INTO v_owner
    FROM pgmq.q_rnaseq_dispatch q WHERE q.msg_id = p_msg_id;
    IF FOUND AND v_owner IS DISTINCT FROM p_run_id::text THEN
        RAISE EXCEPTION 'message % is not for run %', p_msg_id, p_run_id USING ERRCODE = '22023';
    END IF;
END;
$$;

-- Marks a still-queued run failed and archives its message; returns whether the run changed.
CREATE OR REPLACE FUNCTION public.fail_rnaseq_run(
    p_run_id BIGINT,
    p_msg_id BIGINT,
    p_message TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
    v_changed INTEGER;
BEGIN
    IF p_message IS NULL OR btrim(p_message) = '' THEN
        RAISE EXCEPTION 'a failure message is required' USING ERRCODE = '22023';
    END IF;
    PERFORM public._check_rnaseq_message(p_run_id, p_msg_id);

    UPDATE public.rnaseq_runs
    SET status = 'failed',
        message = p_message,
        completed_at = now(),
        updated_at = now()
    WHERE id = p_run_id
      AND status = 'queued';
    GET DIAGNOSTICS v_changed = ROW_COUNT;

    PERFORM pgmq.archive('rnaseq_dispatch', p_msg_id);
    RETURN v_changed > 0;
END;
$$;

-- Records a still-queued run's submitted workflow and deletes its message; returns whether the run changed.
CREATE OR REPLACE FUNCTION public.complete_rnaseq_run(
    p_run_id BIGINT,
    p_msg_id BIGINT,
    p_argo_workflow_name TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
    v_changed INTEGER;
BEGIN
    IF p_argo_workflow_name IS NULL OR btrim(p_argo_workflow_name) = '' THEN
        RAISE EXCEPTION 'argo workflow name is required' USING ERRCODE = '22023';
    END IF;
    PERFORM public._check_rnaseq_message(p_run_id, p_msg_id);

    UPDATE public.rnaseq_runs
    SET status = 'submitted',
        argo_workflow_name = p_argo_workflow_name,
        submitted_at = now(),
        updated_at = now()
    WHERE id = p_run_id
      AND status = 'queued';
    GET DIAGNOSTICS v_changed = ROW_COUNT;

    PERFORM pgmq.delete('rnaseq_dispatch', p_msg_id);
    RETURN v_changed > 0;
END;
$$;

-- Returns the next queued run of any type and hides its message for p_vt seconds, or no row
-- when the queue is empty. Messages it cannot use are dropped on the way: a run that is gone
-- or no longer queued (deleted), a message without a usable run_id (archived), and a run
-- whose message came back more than p_max_reads times (failed and archived).
CREATE OR REPLACE FUNCTION public.claim_rnaseq_run(
    p_vt INTEGER DEFAULT 60,
    p_max_reads INTEGER DEFAULT 5
) RETURNS TABLE(run_id BIGINT, workflow_type TEXT, params JSONB, run_key TEXT, msg_id BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
#variable_conflict use_column
DECLARE
    -- Most messages one call drops before it returns.
    c_max_skipped CONSTANT INTEGER := 100;
    m pgmq.message_record;
    v_id_text TEXT;
    v_run public.rnaseq_runs%ROWTYPE;
BEGIN
    IF p_vt IS NULL OR p_vt < 0 OR p_max_reads IS NULL OR p_max_reads < 1 THEN
        RAISE EXCEPTION 'p_vt must be >= 0 and p_max_reads >= 1' USING ERRCODE = '22023';
    END IF;

    FOR i IN 1..c_max_skipped LOOP
        SELECT * INTO m FROM pgmq.read('rnaseq_dispatch', p_vt, 1) LIMIT 1;
        IF NOT FOUND THEN
            RETURN;
        END IF;

        -- Up to 18 digits always fits a BIGINT.
        v_id_text := m.message ->> 'run_id';
        IF v_id_text IS NULL OR v_id_text !~ '^[0-9]{1,18}$' THEN
            PERFORM pgmq.archive('rnaseq_dispatch', m.msg_id);
            CONTINUE;
        END IF;

        SELECT * INTO v_run FROM public.rnaseq_runs r WHERE r.id = v_id_text::BIGINT;
        IF NOT FOUND OR v_run.status <> 'queued' THEN
            PERFORM pgmq.delete('rnaseq_dispatch', m.msg_id);
            CONTINUE;
        END IF;

        IF m.read_ct > p_max_reads THEN
            PERFORM public.fail_rnaseq_run(
                v_run.id, m.msg_id, format('not submitted after %s attempts', m.read_ct - 1)
            );
            CONTINUE;
        END IF;

        RETURN QUERY SELECT v_run.id, v_run.workflow_type, v_run.params, v_run.run_key, m.msg_id;
        RETURN;
    END LOOP;
END;
$$;

-- Only bloom_workflows may call them; the message check is internal.
REVOKE EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public._check_rnaseq_message(BIGINT, BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.claim_rnaseq_run(INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.complete_rnaseq_run(BIGINT, BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.fail_rnaseq_run(BIGINT, BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.claim_rnaseq_run(INTEGER, INTEGER) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.complete_rnaseq_run(BIGINT, BIGINT, TEXT) TO bloom_workflows;
GRANT EXECUTE ON FUNCTION public.fail_rnaseq_run(BIGINT, BIGINT, TEXT) TO bloom_workflows;

COMMIT;
