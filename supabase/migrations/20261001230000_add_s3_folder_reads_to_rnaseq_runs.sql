-- 20261001230000_add_s3_folder_reads_to_rnaseq_runs.sql
--
-- A Cell Ranger run can read its FASTQs from an S3 folder: params may carry the folder
-- ("fastq_url") and the files found there when the run was started ("fastq_files": name,
-- size and ETag each), so the run reads exactly those files. A run has at most one source:
-- a folder, SRA run IDs, or neither (a registered sample).
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- 1. params may carry fastq_url and fastq_files ----------------------------------------------

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_scrna_cellranger_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_scrna_cellranger_check CHECK (
    workflow_type <> 'scrna-cellranger' OR coalesce(
        jsonb_typeof(params) = 'object'
        AND params - 'sample' - 'reference' - 'sra_runs' - 'fastq_url' - 'fastq_files' = '{}'::jsonb
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
        -- One source at most, and a folder always comes with its files.
        AND NOT (params ? 'sra_runs' AND params ? 'fastq_url')
        AND (params ? 'fastq_url') = (params ? 'fastq_files')
        AND (NOT params ? 'fastq_url' OR (
            jsonb_typeof(params -> 'fastq_url') = 'string'
            AND length(params ->> 'fastq_url') <= 1024
            AND params ->> 'fastq_url'
                ~ '^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/([A-Za-z0-9!_.*''()-]+/)+$'
            AND params ->> 'fastq_url' !~ '/\.{1,2}/'
        ))
        -- 2 to 96 FASTQs, each named for this sample, with a size and an ETag.
        AND (NOT params ? 'fastq_files' OR CASE
            WHEN jsonb_typeof(params -> 'fastq_files') = 'array' THEN
                jsonb_array_length(params -> 'fastq_files') BETWEEN 2 AND 96
                AND NOT jsonb_path_exists(
                    params -> 'fastq_files',
                    'strict $[*] ? (@.type() != "object"
                        || @.name.type() != "string"
                        || !(@.name like_regex "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}_S[0-9]+_L[0-9]{3}_(R1|R2|I1|I2)_001\\.fastq(\\.gz)?$")
                        || !(@.name starts with $prefix)
                        || @.size.type() != "number" || @.size < 0
                        || @.etag.type() != "string")',
                    jsonb_build_object('prefix', (params ->> 'sample') || '_S')
                )
            ELSE false
        END)
        AND run_key = (params ->> 'sample') || '__' || (params ->> 'reference')
                      || '__' || requested_by::text,
        false
    )
);

-- 2. The request function ------------------------------------------------------------------

-- New arguments change the signature, so the old one is dropped rather than replaced.
DROP FUNCTION IF EXISTS public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[]);

-- Creates a queued Cell Ranger run and its dispatch message in one transaction; returns the run id.
-- The reads come from one source: an S3 folder (p_fastq_url with the p_fastq_files found in it),
-- SRA run IDs (p_sra_runs, imported under a name nobody has registered), or neither (a
-- registered sample). No run on a registered name starts while an import of it is still
-- downloading.
CREATE OR REPLACE FUNCTION public.request_scrna_cellranger_run(
    p_sample TEXT,
    p_reference TEXT,
    p_requested_by UUID,
    p_metadata JSONB DEFAULT NULL,
    p_sra_runs TEXT[] DEFAULT NULL,
    p_fastq_url TEXT DEFAULT NULL,
    p_fastq_files JSONB DEFAULT NULL
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
    v_url_rule CONSTANT TEXT := '^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/([A-Za-z0-9!_.*''()-]+/)+$';
    v_fastq_rule CONSTANT TEXT := '^(.+)_S[0-9]+_L([0-9]{3})_(R1|R2|I1|I2)_001\.fastq(\.gz)?$';
    v_max_runs CONSTANT INTEGER := 9;
    v_max_files CONSTANT INTEGER := 96;
    v_max_url CONSTANT INTEGER := 1024;
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
    IF p_sra_runs IS NOT NULL AND p_fastq_url IS NOT NULL THEN
        RAISE EXCEPTION 'give SRA run IDs or an S3 folder, not both' USING ERRCODE = '22023';
    END IF;
    IF (p_fastq_url IS NULL) <> (p_fastq_files IS NULL) THEN
        RAISE EXCEPTION 'an S3 folder needs the files found in it' USING ERRCODE = '22023';
    END IF;

    v_params := jsonb_build_object('sample', p_sample, 'reference', p_reference);

    IF p_fastq_url IS NOT NULL THEN
        IF length(p_fastq_url) > v_max_url OR p_fastq_url !~ v_url_rule
           OR p_fastq_url ~ '/\.{1,2}/' THEN
            RAISE EXCEPTION 'an S3 folder looks like s3://bucket/folder/' USING ERRCODE = '22023';
        END IF;
        IF jsonb_typeof(p_fastq_files) <> 'array'
           OR jsonb_array_length(p_fastq_files) NOT BETWEEN 2 AND v_max_files THEN
            RAISE EXCEPTION 'give 2 to % FASTQs', v_max_files USING ERRCODE = '22023';
        END IF;
        IF EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_fastq_files) f
            WHERE jsonb_typeof(f) <> 'object'
               OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(f) k)
                  <> ARRAY['etag', 'name', 'size']
               OR jsonb_typeof(f -> 'name') <> 'string'
               OR jsonb_typeof(f -> 'etag') <> 'string'
               -- A whole, non-negative byte count; the cast only runs on a number.
               OR CASE WHEN jsonb_typeof(f -> 'size') = 'number'
                       THEN (f ->> 'size')::numeric < 0
                            OR (f ->> 'size')::numeric <> trunc((f ->> 'size')::numeric)
                       ELSE true END
               OR length(f ->> 'etag') NOT BETWEEN 1 AND 200
               OR f ->> 'name' !~ v_fastq_rule
               OR substring(f ->> 'name' FROM v_fastq_rule) <> p_sample
        ) THEN
            RAISE EXCEPTION 'each FASTQ needs a name like %_S1_L001_R1_001.fastq.gz, a size and an ETag', p_sample
                USING ERRCODE = '22023';
        END IF;
        IF (SELECT count(DISTINCT f ->> 'name') FROM jsonb_array_elements(p_fastq_files) f)
           <> jsonb_array_length(p_fastq_files) THEN
            RAISE EXCEPTION 'a FASTQ is listed twice' USING ERRCODE = '22023';
        END IF;
        -- Every lane needs its R1 and R2.
        IF EXISTS (
            SELECT 1
            FROM (
                SELECT (regexp_match(f ->> 'name', v_fastq_rule))[2] AS lane,
                       (regexp_match(f ->> 'name', v_fastq_rule))[3] AS read
                FROM jsonb_array_elements(p_fastq_files) f
            ) r
            GROUP BY lane
            HAVING NOT (bool_or(read = 'R1') AND bool_or(read = 'R2'))
        ) THEN
            RAISE EXCEPTION 'every lane needs an R1 and an R2' USING ERRCODE = '22023';
        END IF;
        v_params := v_params || jsonb_build_object(
            'fastq_url', p_fastq_url,
            'fastq_files', (SELECT jsonb_agg(f ORDER BY f ->> 'name')
                            FROM jsonb_array_elements(p_fastq_files) f)
        );
    ELSE
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
REVOKE EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[], TEXT, JSONB)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[], TEXT, JSONB)
    TO bloom_workflows;

COMMIT;
