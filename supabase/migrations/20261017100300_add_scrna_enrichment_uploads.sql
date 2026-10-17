-- 20261017100300_add_scrna_enrichment_uploads.sql
--
-- Linking an older dataset to its genome version, and uploading enrichment results. A writer
-- or the pipeline links a dataset once (bloomctl checks its genes against the version's GTF
-- first; the database checks species and readiness). An enrichment run is started for a
-- dataset's finished differential expression analysis and a ready annotation build of the
-- dataset's genome version, its JSON file is uploaded to the path it is given, and it is
-- finished with the file's SHA-256 and size, the term count and the gene counts; finishing
-- checks the file is stored at exactly that size and makes the run ready. A run that stops is
-- abandoned. The file's contents are checked by bloomctl before upload. Only the caller's own
-- uploading run's file may be added under enrichment/, and nobody may replace or delete a
-- ready run's file.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

SET LOCAL lock_timeout = '5s';

-- 1. Functions -----------------------------------------------------------------------------

-- Links a dataset with no genome version to a ready version of the dataset's species.
CREATE OR REPLACE FUNCTION public.link_scrna_dataset_genome(p_dataset_id BIGINT, p_version_id BIGINT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_current BIGINT;
    v_dataset_species BIGINT;
    v_status TEXT;
    v_species BIGINT;
BEGIN
    IF auth.uid() IS NULL THEN
        RAISE EXCEPTION 'sign in to link a dataset' USING ERRCODE = '42501';
    END IF;
    SELECT d.genome_version_id, d.species_id INTO v_current, v_dataset_species
      FROM public.scrna_datasets d
     WHERE d.id = p_dataset_id AND d.deleted_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no dataset %', p_dataset_id USING ERRCODE = '22023';
    END IF;
    IF v_current IS NOT NULL THEN
        RAISE EXCEPTION 'dataset % is already linked to genome version %', p_dataset_id, v_current
            USING ERRCODE = '55000';
    END IF;
    SELECT v.status, g.species_id INTO v_status, v_species
      FROM public.genome_reference_versions v
      JOIN public.genome_references g ON g.id = v.genome_id
     WHERE v.id = p_version_id FOR SHARE OF v;
    IF v_status IS DISTINCT FROM 'ready' THEN
        RAISE EXCEPTION 'genome version % is not ready', p_version_id USING ERRCODE = '22023';
    END IF;
    IF v_species IS DISTINCT FROM v_dataset_species THEN
        RAISE EXCEPTION 'genome version % is for species %, not the dataset''s species %',
            p_version_id, v_species, v_dataset_species USING ERRCODE = '22023';
    END IF;
    UPDATE public.scrna_datasets SET genome_version_id = p_version_id WHERE id = p_dataset_id;
END;
$$;

-- Starts an enrichment run; returns it and the path to upload its file to. p_rnaseq_run_id
-- names the pipeline job producing it, a Cell Ranger or enrichment job; leave it out for a run
-- uploaded by hand.
CREATE OR REPLACE FUNCTION public.start_scrna_enrichment_run(
    p_dataset_id BIGINT,
    p_de_run_id BIGINT,
    p_annotation_build_id BIGINT,
    p_params JSONB,
    p_rnaseq_run_id BIGINT DEFAULT NULL
) RETURNS TABLE(run_id BIGINT, file_path TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict use_column
DECLARE
    v_user UUID := auth.uid();
    v_genome BIGINT;
    v_build public.genome_annotation_builds%ROWTYPE;
    v_de_status TEXT;
    v_row public.scrna_enrichment_runs%ROWTYPE;
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'sign in to upload an enrichment' USING ERRCODE = '42501';
    END IF;
    IF p_params IS NULL OR jsonb_typeof(p_params) <> 'object' THEN
        RAISE EXCEPTION 'settings must be a JSON object' USING ERRCODE = '22023';
    END IF;
    SELECT d.genome_version_id INTO v_genome FROM public.scrna_datasets d
     WHERE d.id = p_dataset_id AND d.deleted_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no dataset %', p_dataset_id USING ERRCODE = '22023';
    END IF;
    IF v_genome IS NULL THEN
        RAISE EXCEPTION 'dataset % is not linked to a genome version', p_dataset_id
            USING ERRCODE = '55000';
    END IF;
    SELECT * INTO v_build FROM public.genome_annotation_builds b WHERE b.id = p_annotation_build_id;
    IF NOT FOUND OR v_build.status <> 'ready' THEN
        RAISE EXCEPTION 'annotation build % is not ready', p_annotation_build_id
            USING ERRCODE = '55000';
    END IF;
    IF v_build.version_id <> v_genome THEN
        RAISE EXCEPTION 'annotation build % is for genome version %, not the dataset''s %',
            p_annotation_build_id, v_build.version_id, v_genome USING ERRCODE = '22023';
    END IF;
    SELECT r.status INTO v_de_status FROM public.scrna_de_runs r
     WHERE r.id = p_de_run_id AND r.dataset_id = p_dataset_id;
    IF v_de_status IS DISTINCT FROM 'complete' THEN
        RAISE EXCEPTION 'differential expression % of dataset % is %', p_de_run_id, p_dataset_id,
            coalesce(v_de_status, 'missing') USING ERRCODE = '55000';
    END IF;
    IF p_rnaseq_run_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.rnaseq_runs r
         WHERE r.id = p_rnaseq_run_id AND r.workflow_type IN ('scrna-cellranger', 'scrna-enrichment')
    ) THEN
        RAISE EXCEPTION 'no Cell Ranger or enrichment job %', p_rnaseq_run_id USING ERRCODE = '22023';
    END IF;
    -- scrna_enrichment_runs_guard sets the file path.
    INSERT INTO public.scrna_enrichment_runs (dataset_id, de_run_id, annotation_build_id,
                                              rnaseq_run_id, params, file_path, created_by)
    VALUES (p_dataset_id, p_de_run_id, p_annotation_build_id, p_rnaseq_run_id, p_params, '', v_user)
    RETURNING * INTO v_row;
    RETURN QUERY SELECT v_row.id, v_row.file_path;
END;
$$;

-- The signed-in user's run, locked; refuses one started by someone else.
CREATE OR REPLACE FUNCTION public._own_enrichment_run(p_run_id BIGINT)
RETURNS public.scrna_enrichment_runs
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.scrna_enrichment_runs%ROWTYPE;
BEGIN
    IF auth.uid() IS NULL THEN
        RAISE EXCEPTION 'sign in to upload an enrichment' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_row FROM public.scrna_enrichment_runs r WHERE r.id = p_run_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no enrichment run %', p_run_id USING ERRCODE = '22023';
    END IF;
    IF v_row.created_by <> auth.uid() THEN
        RAISE EXCEPTION 'enrichment run % was started by someone else', p_run_id
            USING ERRCODE = '42501';
    END IF;
    RETURN v_row;
END;
$$;

-- Finishes the caller's run: its file must be stored at exactly the stated size. Records the
-- file's checksum and size, the term count (zero is allowed: nothing was significant), and
-- how many background genes have a term in the annotation.
CREATE OR REPLACE FUNCTION public.finish_scrna_enrichment_run(
    p_run_id BIGINT,
    p_file_sha256 TEXT,
    p_file_bytes BIGINT,
    p_term_count INTEGER,
    p_background_genes INTEGER,
    p_annotated_genes INTEGER
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.scrna_enrichment_runs%ROWTYPE := public._own_enrichment_run(p_run_id);
    v_stored BIGINT;
BEGIN
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'enrichment run % is %, not uploading', p_run_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    IF p_file_sha256 IS NULL OR p_file_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'the checksum must be 64 lowercase hex characters' USING ERRCODE = '22023';
    END IF;
    IF p_term_count IS NULL OR p_term_count < 0 THEN
        RAISE EXCEPTION 'the term count must be zero or more' USING ERRCODE = '22023';
    END IF;
    IF p_background_genes IS NULL OR p_background_genes < 1 THEN
        RAISE EXCEPTION 'the background must have at least one gene' USING ERRCODE = '22023';
    END IF;
    IF p_annotated_genes IS NULL OR p_annotated_genes < 0
       OR p_annotated_genes > p_background_genes THEN
        RAISE EXCEPTION 'annotated genes must be between 0 and the % background genes',
            p_background_genes USING ERRCODE = '22023';
    END IF;
    v_stored := public._storage_object_bytes('scrna', v_row.file_path);
    IF v_stored IS NULL THEN
        RAISE EXCEPTION '% has not been uploaded', v_row.file_path USING ERRCODE = '22023';
    ELSIF v_stored IS DISTINCT FROM p_file_bytes THEN
        RAISE EXCEPTION '% is % bytes in storage, not %', v_row.file_path, v_stored,
            coalesce(p_file_bytes::TEXT, 'none') USING ERRCODE = '22023';
    END IF;
    UPDATE public.scrna_enrichment_runs
       SET status = 'ready', file_sha256 = p_file_sha256, file_bytes = p_file_bytes,
           term_count = p_term_count, background_genes = p_background_genes,
           annotated_genes = p_annotated_genes, ready_at = now()
     WHERE id = p_run_id;
END;
$$;

-- Marks the caller's unfinished run abandoned; returns whether it changed.
CREATE OR REPLACE FUNCTION public.abandon_scrna_enrichment_run(p_run_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.scrna_enrichment_runs%ROWTYPE := public._own_enrichment_run(p_run_id);
BEGIN
    IF v_row.status = 'abandoned' THEN
        RETURN false;
    END IF;
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'enrichment run % is %, not uploading', p_run_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    UPDATE public.scrna_enrichment_runs SET status = 'abandoned' WHERE id = p_run_id;
    RETURN true;
END;
$$;

-- True for the file of a run the caller started that is still uploading.
CREATE OR REPLACE FUNCTION public.may_upload_enrichment_file(p_name TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.scrna_enrichment_runs r
     WHERE r.status = 'uploading' AND r.created_by = auth.uid() AND r.file_path = p_name
  );
$$;

REVOKE EXECUTE ON FUNCTION public.link_scrna_dataset_genome(BIGINT, BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.start_scrna_enrichment_run(BIGINT, BIGINT, BIGINT, JSONB, BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public._own_enrichment_run(BIGINT) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.finish_scrna_enrichment_run(BIGINT, TEXT, BIGINT, INTEGER, INTEGER, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.abandon_scrna_enrichment_run(BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.may_upload_enrichment_file(TEXT) FROM PUBLIC, anon, authenticated;

-- Results come from developer tools (bloomctl, as a writer) or the pipeline.
GRANT EXECUTE ON FUNCTION public.link_scrna_dataset_genome(BIGINT, BIGINT)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.start_scrna_enrichment_run(BIGINT, BIGINT, BIGINT, JSONB, BIGINT)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.finish_scrna_enrichment_run(BIGINT, TEXT, BIGINT, INTEGER, INTEGER, INTEGER)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.abandon_scrna_enrichment_run(BIGINT)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.may_upload_enrichment_file(TEXT) TO bloom_writer, bloom_workflows;

-- 2. Storage -------------------------------------------------------------------------------

-- bloom_writer's blanket INSERT and UPDATE would let it write anywhere in the scrna bucket.
-- Under enrichment/ it may only add its own uploading run's file, and never overwrite one.
DROP POLICY IF EXISTS writer_insert_own_enrichment_file ON storage.objects;
CREATE POLICY writer_insert_own_enrichment_file ON storage.objects
    AS RESTRICTIVE
    FOR INSERT TO bloom_writer
    WITH CHECK (
        bucket_id <> 'scrna' OR name NOT LIKE 'enrichment/%'
        OR public.may_upload_enrichment_file(name)
    );

DROP POLICY IF EXISTS writer_no_enrichment_update ON storage.objects;
CREATE POLICY writer_no_enrichment_update ON storage.objects
    AS RESTRICTIVE
    FOR UPDATE TO bloom_writer
    USING (bucket_id <> 'scrna' OR name NOT LIKE 'enrichment/%')
    WITH CHECK (bucket_id <> 'scrna' OR name NOT LIKE 'enrichment/%');

-- The pipeline adds only its own uploading run's file under enrichment/.
DROP POLICY IF EXISTS workflows_insert_own_enrichment_file ON storage.objects;
CREATE POLICY workflows_insert_own_enrichment_file ON storage.objects
    FOR INSERT TO bloom_workflows
    WITH CHECK (bucket_id = 'scrna' AND name LIKE 'enrichment/%'
                AND public.may_upload_enrichment_file(name));

-- A ready run's file stays as it is, for bloom_admin too.
DROP POLICY IF EXISTS admin_keep_finished_enrichment_files_update ON storage.objects;
CREATE POLICY admin_keep_finished_enrichment_files_update ON storage.objects
    AS RESTRICTIVE
    FOR UPDATE TO bloom_admin
    USING (
        bucket_id <> 'scrna'
        OR NOT EXISTS (
            SELECT 1 FROM public.scrna_enrichment_runs r
            WHERE r.status = 'ready' AND objects.name = r.file_path
        )
    );

DROP POLICY IF EXISTS admin_keep_finished_enrichment_files_delete ON storage.objects;
CREATE POLICY admin_keep_finished_enrichment_files_delete ON storage.objects
    AS RESTRICTIVE
    FOR DELETE TO bloom_admin
    USING (
        bucket_id <> 'scrna'
        OR NOT EXISTS (
            SELECT 1 FROM public.scrna_enrichment_runs r
            WHERE r.status = 'ready' AND objects.name = r.file_path
        )
    );

-- PostgREST serves RPCs from a cached schema.
NOTIFY pgrst, 'reload schema';

COMMIT;
