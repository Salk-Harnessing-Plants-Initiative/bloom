-- 20261004120000_add_cyl_pipeline_run_workflows.sql
--
-- fix-cyl-poller-unconcluded-runs (bloom#1042), PR A: what the cylinder status poller needs to
-- conclude a run whose Argo Workflows were garbage-collected before it saw them finish.
--
-- 1. cyl_pipeline_run_workflows: the last terminal phase (Succeeded/Failed/Error) the poller saw
--    for each (run, workflow). Argo deletes a finished Workflow WORKFLOWS_K8S_TTL_SECONDS after it
--    ends; the poller falls back to this row once a lookup 404s. Keyed by run as well as name,
--    because a GC'd Workflow's generated name can be reused by a later dispatch.
-- 2. record_cyl_pipeline_workflow_phase: the poller's only way to write that table. It raises on
--    a non-terminal phase, and writes nothing for a workflow the run's scan rows never carried.
--    It returns false both then and when the phase is unchanged; only true means a write.
-- 3. close_cyl_pipeline_run_workflow_scans: the run-scoped counterpart of
--    fail_cyl_pipeline_run_scans_without_result, which matches on the workflow name alone. The
--    poller can now close a GC'd workflow's rows, and by then that name may belong to another
--    run. bloomctl keeps the name-only RPC: it runs inside the live workflow that owns the name.
--    It raises on a NULL or blank message, so every row it closes says why.
-- 4. cyl_pipeline_runs.poller_concluded_at, and update_cyl_pipeline_run_status re-created with
--    the same signature: its first terminal write stamps completed_at and poller_concluded_at,
--    and the source-status guard then refuses every later write, so a concluded run is final.
--    A 'partial' that dispatch settled (_settle_cyl_pipeline_run, column still NULL) still gets
--    one confirmation. The column is NULL on every existing row, and stays NULL on a run that
--    dispatch alone settled to 'failed': NULL does not mean "not final".
--
-- Until PR B deploys, the poller running today is the one that confirms. Within a cycle of this
-- migration it writes its usual rollup to every candidate run, so each existing poller-written
-- 'partial' becomes final with today's rules (404'd workflows left out, 'failed'/'partial' not
-- withheld), and its completed_at, which that poller re-stamped every cycle, stays at about the
-- deploy time. Compared with today the only lost correction is a 'partial' that would later
-- have gone back to 'running' because a 404'd workflow was in fact still alive; 'failed' and
-- 'complete' were already final, and that workflow's queued rows were already failed for good.
-- The RPC still returns VOID, so that poller keeps logging "-> partial" for writes it refuses.
-- The new poller (PR B) must not deploy before this migration.
--
-- lock_timeout: ADD COLUMN takes ACCESS EXCLUSIVE on cyl_pipeline_runs and the new foreign key
-- SHARE ROW EXCLUSIVE; the table is polled every 15 s and Realtime-published. Fail fast instead
-- of queueing writers behind a long transaction; a timeout rolls this file back unrecorded, so
-- re-running the deploy is safe.
--
-- REVOKE first: default privileges grant writes on every new relation (see 20260924120000), and
-- EXECUTE on every new function to service_role. Only bloom_admin may write the table directly;
-- the poller writes through the RPCs, which only bloom_workflows may call. The functions are owned
-- by postgres explicitly (as in 20260912110000), whichever role applies the migration.
--
-- Idempotent as the newest migration: CREATE ... IF NOT EXISTS, named constraints guarded in DO
-- blocks, DROP POLICY IF EXISTS, CREATE OR REPLACE FUNCTION.
--
-- NOTIFY pgrst: deploy.yml never restarts `rest`, so reload PostgREST's schema cache explicitly.
--
-- Manual rollback: supabase/rollbacks/20261004120000_add_cyl_pipeline_run_workflows_rollback.sql
-- (redeploy code that doesn't use these objects first).

BEGIN;

SET LOCAL lock_timeout = '5s';

-- 1. Table -----------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.cyl_pipeline_run_workflows (
    run_id BIGINT NOT NULL,
    argo_workflow_name TEXT NOT NULL,
    phase TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
DECLARE
    existing text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO existing
      FROM pg_constraint
     WHERE conname = 'cyl_pipeline_run_workflows_pkey'
       AND conrelid = 'public.cyl_pipeline_run_workflows'::regclass;
    IF existing IS NULL THEN
        ALTER TABLE public.cyl_pipeline_run_workflows
            ADD CONSTRAINT cyl_pipeline_run_workflows_pkey PRIMARY KEY (run_id, argo_workflow_name);
    ELSIF existing <> 'PRIMARY KEY (run_id, argo_workflow_name)' THEN
        RAISE EXCEPTION 'cyl_pipeline_run_workflows_pkey is %, expected PRIMARY KEY (run_id, argo_workflow_name)', existing;
    END IF;
END $$;

DO $$
DECLARE
    existing text;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO existing
      FROM pg_constraint
     WHERE conname = 'cyl_pipeline_run_workflows_run_id_fkey'
       AND conrelid = 'public.cyl_pipeline_run_workflows'::regclass;
    IF existing IS NULL THEN
        ALTER TABLE public.cyl_pipeline_run_workflows
            ADD CONSTRAINT cyl_pipeline_run_workflows_run_id_fkey
            FOREIGN KEY (run_id) REFERENCES public.cyl_pipeline_runs(id);
    ELSIF existing NOT IN ('FOREIGN KEY (run_id) REFERENCES cyl_pipeline_runs(id)',
                           'FOREIGN KEY (run_id) REFERENCES public.cyl_pipeline_runs(id)') THEN
        RAISE EXCEPTION 'cyl_pipeline_run_workflows_run_id_fkey is %, expected FOREIGN KEY (run_id) REFERENCES cyl_pipeline_runs(id)', existing;
    END IF;
END $$;

ALTER TABLE public.cyl_pipeline_run_workflows
    DROP CONSTRAINT IF EXISTS cyl_pipeline_run_workflows_phase_check;
ALTER TABLE public.cyl_pipeline_run_workflows
    ADD CONSTRAINT cyl_pipeline_run_workflows_phase_check
    CHECK (phase IN ('Succeeded', 'Failed', 'Error'));

COMMENT ON TABLE public.cyl_pipeline_run_workflows IS
    'Last terminal Argo phase the status poller observed per (pipeline run, workflow), so the '
    'outcome survives ttlStrategy garbage collection. Written only by '
    'record_cyl_pipeline_workflow_phase.';

ALTER TABLE public.cyl_pipeline_run_workflows ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS admin_all_cyl_pipeline_run_workflows ON public.cyl_pipeline_run_workflows;
CREATE POLICY admin_all_cyl_pipeline_run_workflows ON public.cyl_pipeline_run_workflows
    FOR ALL TO bloom_admin USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS agent_read_cyl_pipeline_run_workflows ON public.cyl_pipeline_run_workflows;
CREATE POLICY agent_read_cyl_pipeline_run_workflows ON public.cyl_pipeline_run_workflows
    FOR SELECT TO bloom_agent USING (true);

DROP POLICY IF EXISTS user_read_cyl_pipeline_run_workflows ON public.cyl_pipeline_run_workflows;
CREATE POLICY user_read_cyl_pipeline_run_workflows ON public.cyl_pipeline_run_workflows
    FOR SELECT TO bloom_user USING (true);

DROP POLICY IF EXISTS workflows_read_cyl_pipeline_run_workflows ON public.cyl_pipeline_run_workflows;
CREATE POLICY workflows_read_cyl_pipeline_run_workflows ON public.cyl_pipeline_run_workflows
    FOR SELECT TO bloom_workflows USING (true);

REVOKE ALL ON public.cyl_pipeline_run_workflows
    FROM PUBLIC, anon, authenticated, service_role,
         bloom_user, bloom_writer, bloom_agent, bloom_admin, bloom_workflows;
GRANT SELECT ON public.cyl_pipeline_run_workflows TO bloom_user, bloom_agent, bloom_workflows;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.cyl_pipeline_run_workflows TO bloom_admin;

-- 2. record_cyl_pipeline_workflow_phase -----------------------------------------------------

CREATE OR REPLACE FUNCTION public.record_cyl_pipeline_workflow_phase(
    p_run_id BIGINT,
    p_argo_workflow_name TEXT,
    p_phase TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_rows integer;
BEGIN
    IF p_phase IS NULL OR p_phase NOT IN ('Succeeded', 'Failed', 'Error') THEN
        RAISE EXCEPTION
            'record_cyl_pipeline_workflow_phase: invalid p_phase %, must be one of '
            'Succeeded/Failed/Error', p_phase;
    END IF;

    -- ON CONFLICT makes concurrent first writes for one key safe; its WHERE leaves a repeated
    -- phase (and its observed_at) untouched, so the row count says whether anything changed.
    INSERT INTO public.cyl_pipeline_run_workflows AS w (run_id, argo_workflow_name, phase)
    SELECT p_run_id, p_argo_workflow_name, p_phase
     WHERE EXISTS (
        SELECT 1 FROM public.cyl_pipeline_run_scans
         WHERE run_id = p_run_id AND argo_workflow_name = p_argo_workflow_name
     )
    ON CONFLICT (run_id, argo_workflow_name) DO UPDATE
        SET phase = EXCLUDED.phase, observed_at = now()
        WHERE w.phase IS DISTINCT FROM EXCLUDED.phase;

    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows > 0;
END;
$$;

ALTER FUNCTION public.record_cyl_pipeline_workflow_phase(BIGINT, TEXT, TEXT) OWNER TO postgres;
REVOKE EXECUTE ON FUNCTION public.record_cyl_pipeline_workflow_phase(BIGINT, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.record_cyl_pipeline_workflow_phase(BIGINT, TEXT, TEXT)
    TO bloom_workflows;

-- 3. close_cyl_pipeline_run_workflow_scans --------------------------------------------------

CREATE OR REPLACE FUNCTION public.close_cyl_pipeline_run_workflow_scans(
    p_run_id BIGINT,
    p_argo_workflow_name TEXT,
    p_error_message TEXT
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_rows integer;
BEGIN
    IF p_error_message IS NULL OR btrim(p_error_message) = '' THEN
        RAISE EXCEPTION
            'close_cyl_pipeline_run_workflow_scans: p_error_message must say why the rows closed';
    END IF;

    UPDATE public.cyl_pipeline_run_scans
       SET status = 'failed', error_message = p_error_message, updated_at = now()
     WHERE run_id = p_run_id
       AND argo_workflow_name = p_argo_workflow_name
       AND status = 'queued';
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows;
END;
$$;

ALTER FUNCTION public.close_cyl_pipeline_run_workflow_scans(BIGINT, TEXT, TEXT) OWNER TO postgres;
REVOKE EXECUTE ON FUNCTION public.close_cyl_pipeline_run_workflow_scans(BIGINT, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.close_cyl_pipeline_run_workflow_scans(BIGINT, TEXT, TEXT)
    TO bloom_workflows;

-- 4. Finality --------------------------------------------------------------------------------

ALTER TABLE public.cyl_pipeline_runs
    ADD COLUMN IF NOT EXISTS poller_concluded_at TIMESTAMPTZ;

COMMENT ON COLUMN public.cyl_pipeline_runs.poller_concluded_at IS
    'When the status poller first wrote a terminal status for this run; once set, '
    'update_cyl_pipeline_run_status leaves the run unchanged.';

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

    -- A concurrent second terminal write blocks on the row lock, then re-checks this WHERE
    -- against the first write's row and matches nothing: a run concludes exactly once.
    UPDATE public.cyl_pipeline_runs
    SET status = p_status,
        done_count = coalesce(p_done_count, done_count),
        failed_count = coalesce(p_failed_count, failed_count),
        completed_at = CASE
            WHEN p_status IN ('complete', 'failed', 'partial') THEN now()
            ELSE completed_at
        END,
        poller_concluded_at = CASE
            WHEN p_status IN ('complete', 'failed', 'partial') THEN now()
            ELSE poller_concluded_at
        END
    WHERE id = p_run_id
      AND (status IN ('submitted', 'running')
           OR (status = 'partial' AND poller_concluded_at IS NULL));
END;
$$;

ALTER FUNCTION public.update_cyl_pipeline_run_status(BIGINT, TEXT, INTEGER, INTEGER) OWNER TO postgres;
REVOKE EXECUTE ON FUNCTION public.update_cyl_pipeline_run_status(BIGINT, TEXT, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.update_cyl_pipeline_run_status(BIGINT, TEXT, INTEGER, INTEGER)
    TO bloom_workflows;

COMMIT;

NOTIFY pgrst, 'reload schema';
