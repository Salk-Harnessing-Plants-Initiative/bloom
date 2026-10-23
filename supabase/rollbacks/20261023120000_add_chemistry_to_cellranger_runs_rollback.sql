-- Rollback for 20261023120000_add_chemistry_to_cellranger_runs.sql: restores the 8-argument
-- request_scrna_cellranger_run from 20261013230000. Runs with params.chemistry keep it.

BEGIN;

DROP FUNCTION IF EXISTS public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[], TEXT, JSONB, BIGINT, TEXT);

CREATE FUNCTION public.request_scrna_cellranger_run(
    p_sample TEXT,
    p_reference TEXT,
    p_requested_by UUID,
    p_metadata JSONB DEFAULT NULL,
    p_sra_runs TEXT[] DEFAULT NULL,
    p_fastq_url TEXT DEFAULT NULL,
    p_fastq_files JSONB DEFAULT NULL,
    p_genome_version_id BIGINT DEFAULT NULL
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
    -- Groups: the sample, the S number, the lane and the read.
    v_fastq_rule CONSTANT TEXT := '^(.+)_S([0-9]+)_L([0-9]{3})_(R1|R2|I1|I2)_001\.fastq(\.gz)?$';
    v_max_runs CONSTANT INTEGER := 9;
    v_max_files CONSTANT INTEGER := 96;
    v_max_url CONSTANT INTEGER := 1024;
    v_params JSONB;
    v_run_id BIGINT;
    v_reference TEXT := p_reference;
    v_genome_status TEXT;
    v_run_key TEXT;
    v_earlier BIGINT;
    v_earlier_sample TEXT;
BEGIN
    IF p_sample IS NULL OR p_sample !~ v_sample_rule OR p_sample ~ '__' THEN
        RAISE EXCEPTION 'invalid sample name: %', p_sample USING ERRCODE = '22023';
    END IF;
    IF p_genome_version_id IS NOT NULL THEN
        IF p_reference IS NOT NULL THEN
            RAISE EXCEPTION 'give a genome version or a reference name, not both'
                USING ERRCODE = '22023';
        END IF;
        -- Held until the run is recorded, so the version can't be withdrawn in between.
        SELECT g.name || '.v' || v.version, v.status INTO v_reference, v_genome_status
        FROM public.genome_reference_versions v
        JOIN public.genome_references g ON g.id = v.genome_id
        WHERE v.id = p_genome_version_id
        FOR SHARE OF v;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'no genome version %', p_genome_version_id USING ERRCODE = '22023';
        END IF;
        IF v_genome_status <> 'ready' THEN
            RAISE EXCEPTION 'genome % is %, not ready; choose a ready version', v_reference,
                v_genome_status USING ERRCODE = '22023';
        END IF;
    END IF;
    IF v_reference IS NULL OR v_reference !~ v_reference_rule OR v_reference ~ '__' THEN
        RAISE EXCEPTION 'invalid reference name: %', v_reference USING ERRCODE = '22023';
    END IF;
    v_run_key := p_sample || '__' || v_reference || '__' || p_requested_by::text;
    IF p_requested_by IS NULL THEN
        RAISE EXCEPTION 'requested_by is required' USING ERRCODE = '22023';
    END IF;
    IF p_sra_runs IS NOT NULL AND p_fastq_url IS NOT NULL THEN
        RAISE EXCEPTION 'give SRA run IDs or an S3 folder, not both' USING ERRCODE = '22023';
    END IF;
    IF p_sra_runs IS NULL AND p_fastq_url IS NULL THEN
        RAISE EXCEPTION 'give an S3 folder or SRA run IDs to read % from', p_sample
            USING ERRCODE = '22023';
    END IF;
    IF (p_fastq_url IS NULL) <> (p_fastq_files IS NULL) THEN
        RAISE EXCEPTION 'an S3 folder needs the files found in it' USING ERRCODE = '22023';
    END IF;

    v_params := jsonb_build_object('sample', p_sample, 'reference', v_reference);

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
        -- One read as both .fastq and .fastq.gz would be counted twice.
        IF (SELECT count(DISTINCT regexp_replace(f ->> 'name', '\.gz$', ''))
            FROM jsonb_array_elements(p_fastq_files) f)
           <> jsonb_array_length(p_fastq_files) THEN
            RAISE EXCEPTION 'a read is listed twice, as .fastq and .fastq.gz' USING ERRCODE = '22023';
        END IF;
        -- Each S number's lanes need their R1 and R2.
        IF EXISTS (
            SELECT 1
            FROM (
                SELECT (regexp_match(f ->> 'name', v_fastq_rule))[2] AS number,
                       (regexp_match(f ->> 'name', v_fastq_rule))[3] AS lane,
                       (regexp_match(f ->> 'name', v_fastq_rule))[4] AS read
                FROM jsonb_array_elements(p_fastq_files) f
            ) r
            GROUP BY number, lane
            HAVING NOT (bool_or(read = 'R1') AND bool_or(read = 'R2'))
        ) THEN
            RAISE EXCEPTION 'every lane of every S number needs an R1 and an R2' USING ERRCODE = '22023';
        END IF;
        -- Serialises folder runs on one key, so two can't both pass the check below.
        PERFORM pg_advisory_xact_lock(hashtextextended('rnaseq_run_key:' || v_run_key, 0));
        SELECT id INTO v_earlier FROM public.rnaseq_runs
        WHERE workflow_type = 'scrna-cellranger'
          AND run_key = v_run_key
          AND status <> 'failed'
        ORDER BY id
        LIMIT 1;
        IF v_earlier IS NOT NULL THEN
            RAISE EXCEPTION '% against % has already been processed (run %). To process these reads as a new sample, rename the FASTQs so they start with a new sample name, e.g. %_rep2_S1_L001_R1_001.fastq.gz, and start again',
                p_sample, v_reference, v_earlier, p_sample
                USING ERRCODE = '23505';
        END IF;
        v_params := v_params || jsonb_build_object(
            'fastq_url', p_fastq_url,
            'fastq_files', (SELECT jsonb_agg(f ORDER BY f ->> 'name')
                            FROM jsonb_array_elements(p_fastq_files) f)
        );
    ELSE
        IF coalesce(array_ndims(p_sra_runs), 0) <> 1
           OR cardinality(p_sra_runs) NOT BETWEEN 1 AND v_max_runs
           OR EXISTS (SELECT 1 FROM unnest(p_sra_runs) r WHERE r IS NULL OR r !~ v_run_id_rule)
           OR (SELECT count(DISTINCT r) FROM unnest(p_sra_runs) r) <> cardinality(p_sra_runs) THEN
            RAISE EXCEPTION 'give 1 to % distinct SRA run IDs like SRR12046049', v_max_runs
                USING ERRCODE = '22023';
        END IF;

        -- Serialises runs on one key, as a folder run does, so two can't both pass the checks.
        PERFORM pg_advisory_xact_lock(hashtextextended('rnaseq_run_key:' || v_run_key, 0));
        -- The same reads against the same reference, by the same scientist, are the same job
        -- under whatever name.
        SELECT id, params ->> 'sample' INTO v_earlier, v_earlier_sample
        FROM public.rnaseq_runs
        WHERE workflow_type = 'scrna-cellranger'
          AND requested_by = p_requested_by
          AND params ->> 'reference' = v_reference
          AND params ? 'sra_runs'
          AND status <> 'failed'
          AND (SELECT array_agg(r ORDER BY r) FROM jsonb_array_elements_text(params -> 'sra_runs') r)
              = (SELECT array_agg(r ORDER BY r) FROM unnest(p_sra_runs) r)
        ORDER BY id
        LIMIT 1;
        IF v_earlier IS NOT NULL THEN
            RAISE EXCEPTION '% against % was already run as % (run %); its results are on that run''s page. To run these reads again, choose another reference',
                array_to_string(p_sra_runs, ', '), v_reference, v_earlier_sample, v_earlier
                USING ERRCODE = '23505';
        END IF;
        -- Another run on this key would share its folder on the cluster.
        SELECT id INTO v_earlier FROM public.rnaseq_runs
        WHERE workflow_type = 'scrna-cellranger'
          AND run_key = v_run_key
          AND status <> 'failed'
        ORDER BY id
        LIMIT 1;
        IF v_earlier IS NOT NULL THEN
            RAISE EXCEPTION '% against % already has run %. Choose another sample name, or another reference',
                p_sample, v_reference, v_earlier
                USING ERRCODE = '23505';
        END IF;
        v_params := v_params || jsonb_build_object('sra_runs', to_jsonb(p_sra_runs));
    END IF;

    INSERT INTO public.rnaseq_runs (
        workflow_type, params, run_key, requested_by, metadata, genome_version_id
    )
    VALUES (
        'scrna-cellranger',
        v_params,
        v_run_key,
        p_requested_by,
        p_metadata,
        p_genome_version_id
    )
    RETURNING id INTO v_run_id;

    PERFORM pgmq.send('rnaseq_dispatch', jsonb_build_object('run_id', v_run_id));

    RETURN v_run_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[], TEXT, JSONB, BIGINT)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[], TEXT, JSONB, BIGINT)
    TO bloom_workflows;

COMMIT;
