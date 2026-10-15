-- 20261016100100_add_genome_annotation_uploads.sql
--
-- Uploading a genome version's annotation. A writer or the pipeline starts a build for a
-- ready version and a source, adds term names, gene -> term rows and gene descriptions in
-- batches, and finishes it with the counts it sent, which makes it ready; a build that stops
-- is abandoned and its rows removed. An admin withdraws a ready build. Each source adds only
-- the kinds of term it produces.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

SET LOCAL lock_timeout = '5s';

-- The kinds of term each source may add.
CREATE OR REPLACE FUNCTION public.genome_annotation_source_kinds(p_source TEXT)
RETURNS TEXT[]
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog
AS $$
    SELECT CASE p_source
        WHEN 'tair-go' THEN ARRAY['go']
        WHEN 'eggnog' THEN ARRAY['go', 'kegg_pathway', 'kegg_ko']
        WHEN 'mercator' THEN ARRAY['mapman']
    END;
$$;

-- Starts the next annotation build of a ready genome version from one source.
CREATE OR REPLACE FUNCTION public.start_genome_annotation_build(
    p_version_id BIGINT,
    p_source TEXT,
    p_tool_versions JSONB DEFAULT '{}'::jsonb
) RETURNS TABLE(build_id BIGINT, build INTEGER)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict use_column
DECLARE
    v_user UUID := auth.uid();
    v_live public.genome_annotation_builds%ROWTYPE;
    v_row public.genome_annotation_builds%ROWTYPE;
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'sign in to upload an annotation' USING ERRCODE = '42501';
    END IF;
    IF p_tool_versions IS NULL OR jsonb_typeof(p_tool_versions) <> 'object' THEN
        RAISE EXCEPTION 'tool versions must be a JSON object' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_live FROM public.genome_annotation_builds b
     WHERE b.version_id = p_version_id AND b.source = p_source
       AND b.status IN ('uploading', 'ready');
    IF FOUND THEN
        RAISE EXCEPTION 'genome version % already has % annotation build % (%)',
            p_version_id, p_source, v_live.build, v_live.status USING ERRCODE = '55000';
    END IF;
    -- genome_annotation_builds_number checks the version is ready and sets the build number;
    -- the table's check refuses an unknown source.
    INSERT INTO public.genome_annotation_builds (version_id, source, build, tool_versions, created_by)
    VALUES (p_version_id, p_source, 0, p_tool_versions, v_user)
    RETURNING * INTO v_row;
    RETURN QUERY SELECT v_row.id, v_row.build;
END;
$$;

-- Adds or renames up to 5,000 terms; returns how many rows were written.
CREATE OR REPLACE FUNCTION public.add_functional_terms(p_terms JSONB)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_count INTEGER;
BEGIN
    IF auth.uid() IS NULL THEN
        RAISE EXCEPTION 'sign in to upload an annotation' USING ERRCODE = '42501';
    END IF;
    IF p_terms IS NULL OR jsonb_typeof(p_terms) <> 'array'
       OR jsonb_array_length(p_terms) NOT BETWEEN 1 AND 5000 THEN
        RAISE EXCEPTION 'send 1 to 5000 terms at a time, as a JSON array' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.functional_terms (kind, term_id, name, namespace)
    SELECT r.kind, r.term_id, r.name, r.namespace
      FROM jsonb_to_recordset(p_terms) AS r(kind TEXT, term_id TEXT, name TEXT, namespace TEXT)
    ON CONFLICT (kind, term_id) DO UPDATE
       SET name = EXCLUDED.name, namespace = EXCLUDED.namespace
     WHERE (functional_terms.name, functional_terms.namespace)
           IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.namespace);
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- The signed-in user's uploading build, locked; refuses one started by someone else or finished.
CREATE OR REPLACE FUNCTION public._own_uploading_annotation_build(p_build_id BIGINT)
RETURNS public.genome_annotation_builds
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_annotation_builds%ROWTYPE;
BEGIN
    IF auth.uid() IS NULL THEN
        RAISE EXCEPTION 'sign in to upload an annotation' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_row FROM public.genome_annotation_builds b WHERE b.id = p_build_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no annotation build %', p_build_id USING ERRCODE = '22023';
    END IF;
    IF v_row.created_by <> auth.uid() THEN
        RAISE EXCEPTION 'annotation build % was started by someone else', p_build_id
            USING ERRCODE = '42501';
    END IF;
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'annotation build % is %, not uploading', p_build_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    RETURN v_row;
END;
$$;

-- Adds up to 5,000 gene -> term rows to the caller's uploading build; returns how many.
CREATE OR REPLACE FUNCTION public.add_genome_gene_terms(p_build_id BIGINT, p_rows JSONB)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_annotation_builds%ROWTYPE := public._own_uploading_annotation_build(p_build_id);
    v_bad TEXT;
    v_count INTEGER;
BEGIN
    IF p_rows IS NULL OR jsonb_typeof(p_rows) <> 'array'
       OR jsonb_array_length(p_rows) NOT BETWEEN 1 AND 5000 THEN
        RAISE EXCEPTION 'send 1 to 5000 rows at a time, as a JSON array' USING ERRCODE = '22023';
    END IF;
    SELECT r.kind INTO v_bad
      FROM jsonb_to_recordset(p_rows) AS r(kind TEXT)
     WHERE r.kind IS NULL OR NOT r.kind = ANY (public.genome_annotation_source_kinds(v_row.source))
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'a % build holds only % terms, not %', v_row.source,
            array_to_string(public.genome_annotation_source_kinds(v_row.source), ', '),
            coalesce(v_bad, 'a missing kind') USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.genome_gene_terms (build_id, gene_id, kind, term_id, direct)
    SELECT p_build_id, r.gene_id, r.kind, r.term_id, coalesce(r.direct, true)
      FROM jsonb_to_recordset(p_rows) AS r(gene_id TEXT, kind TEXT, term_id TEXT, direct BOOLEAN);
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- Adds up to 5,000 gene names and descriptions to the caller's uploading build.
CREATE OR REPLACE FUNCTION public.add_genome_gene_descriptions(p_build_id BIGINT, p_rows JSONB)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_annotation_builds%ROWTYPE := public._own_uploading_annotation_build(p_build_id);
    v_count INTEGER;
BEGIN
    IF p_rows IS NULL OR jsonb_typeof(p_rows) <> 'array'
       OR jsonb_array_length(p_rows) NOT BETWEEN 1 AND 5000 THEN
        RAISE EXCEPTION 'send 1 to 5000 rows at a time, as a JSON array' USING ERRCODE = '22023';
    END IF;
    INSERT INTO public.genome_gene_descriptions (build_id, gene_id, gene_name, description)
    SELECT p_build_id, r.gene_id, r.gene_name, r.description
      FROM jsonb_to_recordset(p_rows) AS r(gene_id TEXT, gene_name TEXT, description TEXT);
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- Finishes the caller's build: it must hold exactly the genes and rows the uploader sent.
-- Makes the build ready; returns its number.
CREATE OR REPLACE FUNCTION public.finish_genome_annotation_build(
    p_build_id BIGINT,
    p_gene_count INTEGER,
    p_term_count BIGINT
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_annotation_builds%ROWTYPE := public._own_uploading_annotation_build(p_build_id);
    v_genes INTEGER;
    v_terms BIGINT;
BEGIN
    SELECT count(DISTINCT t.gene_id), count(*) INTO v_genes, v_terms
      FROM public.genome_gene_terms t WHERE t.build_id = p_build_id;
    IF p_gene_count IS NULL OR p_term_count IS NULL OR v_terms < 1
       OR v_genes <> p_gene_count OR v_terms <> p_term_count THEN
        RAISE EXCEPTION 'annotation build % holds % genes and % rows, not % and %', p_build_id,
            v_genes, v_terms, coalesce(p_gene_count::TEXT, 'none'),
            coalesce(p_term_count::TEXT, 'none') USING ERRCODE = '22023';
    END IF;
    UPDATE public.genome_annotation_builds
       SET status = 'ready', gene_count = v_genes, term_count = v_terms, ready_at = now()
     WHERE id = p_build_id;
    RETURN v_row.build;
END;
$$;

-- Marks the caller's unfinished build abandoned and removes its rows; returns whether it changed.
CREATE OR REPLACE FUNCTION public.abandon_genome_annotation_build(p_build_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_annotation_builds%ROWTYPE;
BEGIN
    IF auth.uid() IS NULL THEN
        RAISE EXCEPTION 'sign in to upload an annotation' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_row FROM public.genome_annotation_builds b WHERE b.id = p_build_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no annotation build %', p_build_id USING ERRCODE = '22023';
    END IF;
    IF v_row.created_by <> auth.uid() THEN
        RAISE EXCEPTION 'annotation build % was started by someone else', p_build_id
            USING ERRCODE = '42501';
    END IF;
    IF v_row.status = 'abandoned' THEN
        RETURN false;
    END IF;
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'annotation build % is %, not uploading', p_build_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    DELETE FROM public.genome_gene_terms WHERE build_id = p_build_id;
    DELETE FROM public.genome_gene_descriptions WHERE build_id = p_build_id;
    UPDATE public.genome_annotation_builds SET status = 'abandoned' WHERE id = p_build_id;
    RETURN true;
END;
$$;

-- Withdraws a ready build, with a reason, so a new build can be made. Runs with the caller's
-- rights, so only bloom_admin can change the row.
CREATE OR REPLACE FUNCTION public.withdraw_genome_annotation_build(p_build_id BIGINT, p_reason TEXT)
RETURNS VOID
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    UPDATE public.genome_annotation_builds
       SET status = 'withdrawn', withdrawn_reason = p_reason
     WHERE id = p_build_id AND status = 'ready';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no ready annotation build %', p_build_id USING ERRCODE = '22023';
    END IF;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.genome_annotation_source_kinds(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.start_genome_annotation_build(BIGINT, TEXT, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.add_functional_terms(JSONB) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public._own_uploading_annotation_build(BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.add_genome_gene_terms(BIGINT, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.add_genome_gene_descriptions(BIGINT, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.finish_genome_annotation_build(BIGINT, INTEGER, BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.abandon_genome_annotation_build(BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.withdraw_genome_annotation_build(BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;

-- Annotations come from developer tools (bloomctl, as a writer) or the pipeline.
DO $$
DECLARE
  v_fn TEXT;
BEGIN
  FOREACH v_fn IN ARRAY ARRAY[
    'start_genome_annotation_build(BIGINT, TEXT, JSONB)', 'add_functional_terms(JSONB)',
    'add_genome_gene_terms(BIGINT, JSONB)', 'add_genome_gene_descriptions(BIGINT, JSONB)',
    'finish_genome_annotation_build(BIGINT, INTEGER, BIGINT)', 'abandon_genome_annotation_build(BIGINT)'
  ] LOOP
    EXECUTE format('GRANT EXECUTE ON FUNCTION public.%s TO bloom_writer, bloom_workflows', v_fn);
  END LOOP;
END
$$;
GRANT EXECUTE ON FUNCTION public.withdraw_genome_annotation_build(BIGINT, TEXT) TO bloom_admin;

COMMIT;
