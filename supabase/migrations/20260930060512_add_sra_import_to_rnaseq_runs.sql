-- 20260930060512_add_sra_import_to_rnaseq_runs.sql
--
-- A Cell Ranger run can import its sample from SRA: params may carry its SRA run IDs
-- ("sra_runs"), the workflow's first step downloads them, and the sample is registered in
-- rnaseq_samples once that download succeeds. Also allows the steps the run now reports:
-- fetch-sra first, and preprocess, cluster and build-h5ad after count.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- 1. params may carry sra_runs ----------------------------------------------------------

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

-- 2. The steps a run reports, in order -----------------------------------------------------

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_current_step_check CHECK (
    current_step IS NULL
    OR (workflow_type = 'scrna-cellranger'
        AND current_step IN ('fetch-sra', 'stage-reference', 'stage', 'qc', 'count',
                             'preprocess', 'cluster', 'build-h5ad', 'cleanup'))
);

-- 3. The request function ------------------------------------------------------------------

-- A new argument changes the signature, so the old one is dropped rather than replaced.
DROP FUNCTION IF EXISTS public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB);

-- Creates a queued Cell Ranger run and its dispatch message in one transaction; returns the run id.
-- With p_sra_runs, the sample is imported from SRA under a name nobody has registered. No run
-- starts on a name while an import of it is still downloading.
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

-- 4. Registering an imported sample ----------------------------------------------------------

-- Registers the sample an SRA run imported, once its download step has succeeded (the run is
-- then running or succeeded). The name, run IDs and scientist come from the run itself; only
-- the counts come from the caller. Registering again returns the same sample, with its first
-- counts. Returns the sample id.
CREATE OR REPLACE FUNCTION public.register_rnaseq_sample(
    p_run_id BIGINT,
    p_fastq_count INTEGER,
    p_total_bytes BIGINT
) RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_run public.rnaseq_runs%ROWTYPE;
    v_name TEXT;
    v_source_ref TEXT;
    v_sample public.rnaseq_samples%ROWTYPE;
BEGIN
    IF p_run_id IS NULL THEN
        RAISE EXCEPTION 'run id is required' USING ERRCODE = '22023';
    END IF;
    IF p_fastq_count IS NULL OR p_fastq_count < 1 THEN
        RAISE EXCEPTION 'fastq count must be at least 1, not %', p_fastq_count USING ERRCODE = '22023';
    END IF;
    IF p_total_bytes IS NULL OR p_total_bytes < 1 THEN
        RAISE EXCEPTION 'total bytes must be at least 1, not %', p_total_bytes USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_run FROM public.rnaseq_runs WHERE id = p_run_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no run %', p_run_id USING ERRCODE = 'P0002';
    END IF;
    IF v_run.workflow_type <> 'scrna-cellranger' OR NOT v_run.params ? 'sra_runs' THEN
        RAISE EXCEPTION 'run % does not import its sample from SRA', p_run_id USING ERRCODE = '22023';
    END IF;
    IF v_run.status NOT IN ('running', 'succeeded') THEN
        RAISE EXCEPTION 'run % is %, so its download has not succeeded', p_run_id, v_run.status
            USING ERRCODE = '55000';
    END IF;

    v_name := v_run.params ->> 'sample';
    v_source_ref := array_to_string(
        ARRAY(SELECT jsonb_array_elements_text(v_run.params -> 'sra_runs')), ','
    );

    INSERT INTO public.rnaseq_samples (name, source, source_ref, fastq_count, total_bytes, registered_by)
    VALUES (v_name, 'sra', v_source_ref, p_fastq_count, p_total_bytes, v_run.requested_by)
    ON CONFLICT (name) DO NOTHING;

    SELECT * INTO v_sample FROM public.rnaseq_samples WHERE name = v_name;
    IF v_sample.source <> 'sra' OR v_sample.source_ref IS DISTINCT FROM v_source_ref THEN
        RAISE EXCEPTION 'sample % is already registered with other SRA run IDs or another source', v_name
            USING ERRCODE = '23505';
    END IF;
    RETURN v_sample.id;
END;
$$;

-- Only bloom_workflows may call it.
REVOKE EXECUTE ON FUNCTION public.register_rnaseq_sample(BIGINT, INTEGER, BIGINT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.register_rnaseq_sample(BIGINT, INTEGER, BIGINT) TO bloom_workflows;

COMMIT;
