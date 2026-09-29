-- Rollback for 20260929195736_add_rnaseq_run_metadata.sql (run by hand).
-- Restores the three-argument request function and drops the column and what it held.

BEGIN;

DROP FUNCTION IF EXISTS public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB);

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

REVOKE EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID) TO bloom_workflows;

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_metadata_check;
ALTER TABLE public.rnaseq_runs DROP COLUMN IF EXISTS metadata;

COMMIT;
