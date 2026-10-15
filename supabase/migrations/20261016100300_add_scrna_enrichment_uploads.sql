-- 20261016100300_add_scrna_enrichment_uploads.sql
--
-- Linking an older dataset to its genome version, and uploading enrichment results. A writer
-- or the pipeline links a dataset once (bloomctl checks its genes against the version's GTF
-- first; the database checks species and readiness). An enrichment run is started for a
-- dataset's finished differential expression analysis and a ready annotation build of the
-- dataset's genome version, its significant terms are added in batches, and it is finished
-- with the count sent, which makes it ready; a run that stops is abandoned and its terms removed.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

SET LOCAL lock_timeout = '5s';

-- Links a dataset with no genome version to one; scrna_datasets_keep_genome_version checks
-- the version is ready and of the dataset's species.
CREATE OR REPLACE FUNCTION public.link_scrna_dataset_genome(p_dataset_id BIGINT, p_version_id BIGINT)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_current BIGINT;
BEGIN
    IF auth.uid() IS NULL THEN
        RAISE EXCEPTION 'sign in to link a dataset' USING ERRCODE = '42501';
    END IF;
    SELECT d.genome_version_id INTO v_current FROM public.scrna_datasets d
     WHERE d.id = p_dataset_id AND d.deleted_at IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no dataset %', p_dataset_id USING ERRCODE = '22023';
    END IF;
    IF v_current IS NOT NULL THEN
        RAISE EXCEPTION 'dataset % is already linked to genome version %', p_dataset_id, v_current
            USING ERRCODE = '55000';
    END IF;
    UPDATE public.scrna_datasets SET genome_version_id = p_version_id WHERE id = p_dataset_id;
END;
$$;

-- Starts an enrichment run; returns its id.
CREATE OR REPLACE FUNCTION public.start_scrna_enrichment_run(
    p_dataset_id BIGINT,
    p_de_run_id BIGINT,
    p_annotation_build_id BIGINT,
    p_params JSONB
) RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_user UUID := auth.uid();
    v_genome BIGINT;
    v_build public.genome_annotation_builds%ROWTYPE;
    v_de_status TEXT;
    v_id BIGINT;
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
    INSERT INTO public.scrna_enrichment_runs (dataset_id, de_run_id, annotation_build_id, params,
                                              created_by)
    VALUES (p_dataset_id, p_de_run_id, p_annotation_build_id, p_params, v_user)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

-- The signed-in user's uploading enrichment run, locked.
CREATE OR REPLACE FUNCTION public._own_uploading_enrichment_run(p_run_id BIGINT)
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
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'enrichment run % is %, not uploading', p_run_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    RETURN v_row;
END;
$$;

-- Adds up to 5,000 terms to the caller's uploading run. Each names a comparison of the run's
-- analysis, a kind of term the annotation's source produces, and genes of the dataset.
CREATE OR REPLACE FUNCTION public.add_scrna_enrichment_terms(p_run_id BIGINT, p_rows JSONB)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_run public.scrna_enrichment_runs%ROWTYPE := public._own_uploading_enrichment_run(p_run_id);
    v_kinds TEXT[];
    v_count INTEGER;
BEGIN
    IF p_rows IS NULL OR jsonb_typeof(p_rows) <> 'array'
       OR jsonb_array_length(p_rows) NOT BETWEEN 1 AND 5000 THEN
        RAISE EXCEPTION 'send 1 to 5000 terms at a time, as a JSON array' USING ERRCODE = '22023';
    END IF;
    SELECT public.genome_annotation_source_kinds(b.source) INTO v_kinds
      FROM public.genome_annotation_builds b WHERE b.id = v_run.annotation_build_id;

    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_rows) AS t(kind TEXT)
         WHERE t.kind IS NULL OR NOT t.kind = ANY (v_kinds)
    ) THEN
        RAISE EXCEPTION 'this annotation holds only % terms', array_to_string(v_kinds, ', ')
            USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_rows) AS t(de_id BIGINT)
         WHERE NOT EXISTS (
             SELECT 1 FROM public.scrna_de c
              WHERE c.id = t.de_id AND c.dataset_id = v_run.dataset_id AND c.run_id = v_run.de_run_id)
    ) THEN
        RAISE EXCEPTION 'every term must name a comparison of differential expression %',
            v_run.de_run_id USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_rows) AS t(gene_ids BIGINT[]), unnest(t.gene_ids) AS g(id)
         WHERE NOT EXISTS (
             SELECT 1 FROM public.scrna_genes s WHERE s.id = g.id AND s.dataset_id = v_run.dataset_id)
    ) THEN
        RAISE EXCEPTION 'every gene must be a gene of dataset %', v_run.dataset_id
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO public.scrna_enrichment_terms (
        run_id, dataset_id, de_id, direction, kind, term_id, hit_count, list_size, term_size,
        background_size, pvalue, fdr, gene_ids
    )
    SELECT p_run_id, v_run.dataset_id, t.de_id, t.direction, t.kind, t.term_id, t.hit_count,
           t.list_size, t.term_size, t.background_size, t.pvalue, t.fdr, t.gene_ids
      FROM jsonb_to_recordset(p_rows) AS t(
          de_id BIGINT, direction TEXT, kind TEXT, term_id TEXT, hit_count INTEGER,
          list_size INTEGER, term_size INTEGER, background_size INTEGER,
          pvalue DOUBLE PRECISION, fdr DOUBLE PRECISION, gene_ids BIGINT[]);
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- Finishes the caller's run: it must hold exactly the terms sent (zero is allowed: nothing was
-- significant). Makes the run ready.
CREATE OR REPLACE FUNCTION public.finish_scrna_enrichment_run(p_run_id BIGINT, p_term_count INTEGER)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_run public.scrna_enrichment_runs%ROWTYPE := public._own_uploading_enrichment_run(p_run_id);
    v_terms INTEGER;
BEGIN
    SELECT count(*) INTO v_terms FROM public.scrna_enrichment_terms t WHERE t.run_id = v_run.id;
    IF p_term_count IS NULL OR v_terms <> p_term_count THEN
        RAISE EXCEPTION 'enrichment run % holds % terms, not %', p_run_id, v_terms,
            coalesce(p_term_count::TEXT, 'none') USING ERRCODE = '22023';
    END IF;
    UPDATE public.scrna_enrichment_runs
       SET status = 'ready', term_count = v_terms, ready_at = now()
     WHERE id = p_run_id;
END;
$$;

-- Marks the caller's unfinished run abandoned and removes its terms; returns whether it changed.
CREATE OR REPLACE FUNCTION public.abandon_scrna_enrichment_run(p_run_id BIGINT)
RETURNS BOOLEAN
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
    IF v_row.status = 'abandoned' THEN
        RETURN false;
    END IF;
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'enrichment run % is %, not uploading', p_run_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    DELETE FROM public.scrna_enrichment_terms WHERE run_id = p_run_id;
    UPDATE public.scrna_enrichment_runs SET status = 'abandoned' WHERE id = p_run_id;
    RETURN true;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.link_scrna_dataset_genome(BIGINT, BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.start_scrna_enrichment_run(BIGINT, BIGINT, BIGINT, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public._own_uploading_enrichment_run(BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.add_scrna_enrichment_terms(BIGINT, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.finish_scrna_enrichment_run(BIGINT, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.abandon_scrna_enrichment_run(BIGINT)
    FROM PUBLIC, anon, authenticated;

-- Results come from developer tools (bloomctl, as a writer) or the pipeline.
GRANT EXECUTE ON FUNCTION public.link_scrna_dataset_genome(BIGINT, BIGINT)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.start_scrna_enrichment_run(BIGINT, BIGINT, BIGINT, JSONB)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.add_scrna_enrichment_terms(BIGINT, JSONB)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.finish_scrna_enrichment_run(BIGINT, INTEGER)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.abandon_scrna_enrichment_run(BIGINT)
    TO bloom_writer, bloom_workflows;

COMMIT;
