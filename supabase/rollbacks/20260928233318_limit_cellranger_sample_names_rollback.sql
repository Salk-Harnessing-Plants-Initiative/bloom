-- Rollback for 20260928233318_limit_cellranger_sample_names.sql (run by hand).
-- Puts back the looser Cell Ranger sample rule (up to 100 characters, '.' allowed) from
-- 20260928213930 in the rnaseq_runs check and request_scrna_cellranger_run.

BEGIN;

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

REVOKE EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID) TO bloom_workflows;

COMMIT;
