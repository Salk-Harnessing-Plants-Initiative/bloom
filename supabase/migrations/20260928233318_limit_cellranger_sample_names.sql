-- 20260928233318_limit_cellranger_sample_names.sql
--
-- Cell Ranger names each run after its sample, and accepts only letters, digits, '_' and
-- '-', at most 64 characters. Cell Ranger sample names now follow that rule, so a name it
-- cannot use is refused when the run is requested. Reference names are unchanged.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- Stops before changing anything if a stored run already breaks the new rule.
DO $$
DECLARE
  v_bad INTEGER;
BEGIN
  SELECT count(*) INTO v_bad
  FROM public.rnaseq_runs
  WHERE workflow_type = 'scrna-cellranger'
    AND params ->> 'sample' !~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$';
  IF v_bad > 0 THEN
    RAISE EXCEPTION '% Cell Ranger run(s) have a sample name Cell Ranger cannot use', v_bad;
  END IF;
END
$$;

-- Cell Ranger inputs: a sample Cell Ranger can use as a run id, a safe reference name, and
-- the run_key built from them.
ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_scrna_cellranger_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_scrna_cellranger_check CHECK (
    workflow_type <> 'scrna-cellranger' OR coalesce(
        jsonb_typeof(params) = 'object'
        AND params - 'sample' - 'reference' = '{}'::jsonb
        AND jsonb_typeof(params -> 'sample') = 'string'
        AND jsonb_typeof(params -> 'reference') = 'string'
        AND params ->> 'sample' ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'
        AND params ->> 'sample' !~ '__'
        AND params ->> 'reference' ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'
        AND params ->> 'reference' !~ '__'
        AND run_key = (params ->> 'sample') || '__' || (params ->> 'reference')
                      || '__' || requested_by::text,
        false
    )
);

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
    -- A sample is also Cell Ranger's run id: letters, digits, '_' or '-', at most 64.
    v_sample_rule CONSTANT TEXT := '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$';
    v_reference_rule CONSTANT TEXT := '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$';
    v_run_id BIGINT;
BEGIN
    IF p_sample IS NULL OR p_sample !~ v_sample_rule OR p_sample ~ '__' THEN
        RAISE EXCEPTION 'invalid sample name: %', p_sample USING ERRCODE = '22023';
    END IF;
    IF p_reference IS NULL OR p_reference !~ v_reference_rule OR p_reference ~ '__' THEN
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

-- Only bloom_workflows may call it.
REVOKE EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID) TO bloom_workflows;

COMMIT;
