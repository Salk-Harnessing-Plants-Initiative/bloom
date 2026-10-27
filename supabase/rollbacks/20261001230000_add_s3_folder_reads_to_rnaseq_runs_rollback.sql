-- Rollback for 20261001230000_add_s3_folder_reads_to_rnaseq_runs.sql (run by hand).
-- Restores the five-argument request function and the earlier params check.

BEGIN;

-- The old check would refuse these rows, so stop before changing anything.
DO $$
DECLARE
  v_bad INTEGER;
BEGIN
  SELECT count(*) INTO v_bad FROM public.rnaseq_runs WHERE params ? 'fastq_url';
  IF v_bad > 0 THEN
    RAISE EXCEPTION '% run(s) read from an S3 folder; remove them first', v_bad;
  END IF;
END
$$;

DROP FUNCTION IF EXISTS public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[], TEXT, JSONB);

CREATE OR REPLACE FUNCTION public.request_scrna_cellranger_run(
    p_sample TEXT,
    p_reference TEXT,
    p_requested_by UUID,
    p_metadata JSONB DEFAULT NULL,
    p_sra_runs TEXT[] DEFAULT NULL
) RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pgmq
AS $$
DECLARE
    -- A sample is also Cell Ranger's run id: letters, digits, '_' or '-', at most 64.
    v_sample_rule CONSTANT TEXT := '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$';
    v_reference_rule CONSTANT TEXT := '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$';
    v_run_id_rule CONSTANT TEXT := '^[SED]RR[0-9]{6,10}$';
    v_max_runs CONSTANT INTEGER := 9;
    v_params JSONB;
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

    v_params := jsonb_build_object('sample', p_sample, 'reference', p_reference);

    IF p_sra_runs IS NOT NULL AND (
        coalesce(array_ndims(p_sra_runs), 0) <> 1
        OR cardinality(p_sra_runs) NOT BETWEEN 1 AND v_max_runs
        OR EXISTS (SELECT 1 FROM unnest(p_sra_runs) r WHERE r IS NULL OR r !~ v_run_id_rule)
        OR (SELECT count(DISTINCT r) FROM unnest(p_sra_runs) r) <> cardinality(p_sra_runs)
    ) THEN
        RAISE EXCEPTION 'give 1 to % distinct SRA run IDs like SRR12046049', v_max_runs
            USING ERRCODE = '22023';
    END IF;

    -- Serialises requests for one name, so two can't both pass the checks below.
    PERFORM pg_advisory_xact_lock(hashtextextended('rnaseq_sra_import:' || p_sample, 0));
    IF EXISTS (
        SELECT 1 FROM public.rnaseq_runs
        WHERE workflow_type = 'scrna-cellranger'
          AND params ->> 'sample' = p_sample
          AND params ? 'sra_runs'
          AND status IN ('queued', 'submitted', 'running')
    ) THEN
        IF p_sra_runs IS NULL THEN
            RAISE EXCEPTION 'sample % is still being imported from SRA; start the run once it is registered', p_sample
                USING ERRCODE = '55000';
        END IF;
        RAISE EXCEPTION 'sample % is already being imported; choose another name', p_sample
            USING ERRCODE = '23505';
    END IF;

    IF p_sra_runs IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM public.rnaseq_samples WHERE name = p_sample) THEN
            RAISE EXCEPTION 'sample % is already registered; choose another name', p_sample
                USING ERRCODE = '23505';
        END IF;
        v_params := v_params || jsonb_build_object('sra_runs', to_jsonb(p_sra_runs));
    END IF;

    INSERT INTO public.rnaseq_runs (workflow_type, params, run_key, requested_by, metadata)
    VALUES (
        'scrna-cellranger',
        v_params,
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
REVOKE EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[])
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[])
    TO bloom_workflows;

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_scrna_cellranger_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_scrna_cellranger_check CHECK (
    workflow_type <> 'scrna-cellranger' OR coalesce(
        jsonb_typeof(params) = 'object'
        AND params - 'sample' - 'reference' - 'sra_runs' = '{}'::jsonb
        AND jsonb_typeof(params -> 'sample') = 'string'
        AND jsonb_typeof(params -> 'reference') = 'string'
        AND params ->> 'sample' ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'
        AND params ->> 'sample' !~ '__'
        AND params ->> 'reference' ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'
        AND params ->> 'reference' !~ '__'
        -- 1 to 9 SRA run IDs (one lane each); the request function also refuses repeats.
        AND (NOT params ? 'sra_runs' OR CASE
            WHEN jsonb_typeof(params -> 'sra_runs') = 'array' THEN
                jsonb_array_length(params -> 'sra_runs') BETWEEN 1 AND 9
                AND NOT jsonb_path_exists(
                    params -> 'sra_runs',
                    'strict $[*] ? (@.type() != "string" || !(@ like_regex "^[SED]RR[0-9]{6,10}$"))'
                )
            ELSE false
        END)
        AND run_key = (params ->> 'sample') || '__' || (params ->> 'reference')
                      || '__' || requested_by::text,
        false
    )
);

COMMIT;
