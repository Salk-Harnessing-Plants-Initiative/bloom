-- 20260929195736_add_rnaseq_run_metadata.sql
--
-- Adds rnaseq_runs.metadata: what the scientist says about the dataset a run will make
-- (species, dataset name, accession, experiment, conditions), kept with the run until its
-- results are loaded into scrna_datasets. params stays the pipeline's inputs only.
-- request_scrna_cellranger_run gains an optional p_metadata; calls without it still work.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- 1. The column -----------------------------------------------------------------------

ALTER TABLE public.rnaseq_runs ADD COLUMN IF NOT EXISTS metadata JSONB;

-- A JSON object of at most 64 KB, or NULL.
ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_metadata_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_metadata_check CHECK (
    metadata IS NULL
    OR (jsonb_typeof(metadata) = 'object' AND octet_length(metadata::text) <= 65536)
);

-- 2. The request function ---------------------------------------------------------------

-- A new argument changes the signature, so the old one is dropped rather than replaced.
DROP FUNCTION IF EXISTS public.request_scrna_cellranger_run(TEXT, TEXT, UUID);

-- Creates a queued Cell Ranger run and its dispatch message in one transaction; returns the run id.
CREATE OR REPLACE FUNCTION public.request_scrna_cellranger_run(
    p_sample TEXT,
    p_reference TEXT,
    p_requested_by UUID,
    p_metadata JSONB DEFAULT NULL
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

    INSERT INTO public.rnaseq_runs (workflow_type, params, run_key, requested_by, metadata)
    VALUES (
        'scrna-cellranger',
        jsonb_build_object('sample', p_sample, 'reference', p_reference),
        p_sample || '__' || p_reference || '__' || p_requested_by::text,
        p_requested_by,
        p_metadata
    )
    RETURNING id INTO v_run_id;

    PERFORM pgmq.send('rnaseq_dispatch', jsonb_build_object('run_id', v_run_id));

    RETURN v_run_id;
END;
$$;

-- Only bloom_workflows may call it.
REVOKE EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB)
    TO bloom_workflows;

COMMIT;
