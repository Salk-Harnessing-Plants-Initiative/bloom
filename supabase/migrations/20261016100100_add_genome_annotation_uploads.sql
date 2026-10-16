-- 20261016100100_add_genome_annotation_uploads.sql
--
-- Uploading a genome version's annotation file. A writer or the pipeline starts a build for a
-- ready version and a source, uploads the build's JSON file to the path it is given, and
-- finishes it with the file's SHA-256 and size and the gene and term counts; finishing checks
-- the file is stored at exactly that size and makes the build ready. A build that stops is
-- abandoned; an admin withdraws a ready one. The file's contents are checked by bloomctl
-- before upload. Only the caller's own uploading build's file may be added, and nobody may
-- replace or delete a ready or withdrawn build's file.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

SET LOCAL lock_timeout = '5s';

-- 1. Upload functions ----------------------------------------------------------------------

-- Starts the next annotation build of a ready genome version from one source; returns the
-- build and the path to upload its file to.
CREATE OR REPLACE FUNCTION public.start_genome_annotation_build(
    p_version_id BIGINT,
    p_source TEXT,
    p_tool_versions JSONB DEFAULT '{}'::jsonb
) RETURNS TABLE(build_id BIGINT, build INTEGER, file_path TEXT)
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
    -- genome_annotation_builds_number checks the version is ready and sets the number and path;
    -- the table's check refuses an unknown source.
    INSERT INTO public.genome_annotation_builds (version_id, source, build, tool_versions,
                                                 file_path, created_by)
    VALUES (p_version_id, p_source, 0, p_tool_versions, '', v_user)
    RETURNING * INTO v_row;
    RETURN QUERY SELECT v_row.id, v_row.build, v_row.file_path;
END;
$$;

-- Size of one stored object, or NULL when it is not there.
CREATE OR REPLACE FUNCTION public._storage_object_bytes(p_bucket TEXT, p_name TEXT)
RETURNS BIGINT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT (o.metadata ->> 'size')::BIGINT
      FROM storage.objects o
     WHERE o.bucket_id = p_bucket AND o.name = p_name;
$$;

-- The signed-in user's build, locked; refuses one started by someone else.
CREATE OR REPLACE FUNCTION public._own_annotation_build(p_build_id BIGINT)
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
    RETURN v_row;
END;
$$;

-- Finishes the caller's build: its file must be stored at exactly the stated size. Records the
-- file's checksum and size and the counts the uploader read from it; returns the build number.
CREATE OR REPLACE FUNCTION public.finish_genome_annotation_build(
    p_build_id BIGINT,
    p_file_sha256 TEXT,
    p_file_bytes BIGINT,
    p_gene_count INTEGER,
    p_term_count BIGINT
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_annotation_builds%ROWTYPE := public._own_annotation_build(p_build_id);
    v_stored BIGINT;
BEGIN
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'annotation build % is %, not uploading', p_build_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    IF p_file_sha256 IS NULL OR p_file_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'the checksum must be 64 lowercase hex characters' USING ERRCODE = '22023';
    END IF;
    IF p_gene_count IS NULL OR p_gene_count < 1 OR p_term_count IS NULL OR p_term_count < 1 THEN
        RAISE EXCEPTION 'an annotation needs at least one gene and one term' USING ERRCODE = '22023';
    END IF;
    v_stored := public._storage_object_bytes('genome-references', v_row.file_path);
    IF v_stored IS NULL THEN
        RAISE EXCEPTION '% has not been uploaded', v_row.file_path USING ERRCODE = '22023';
    ELSIF v_stored IS DISTINCT FROM p_file_bytes THEN
        RAISE EXCEPTION '% is % bytes in storage, not %', v_row.file_path, v_stored,
            coalesce(p_file_bytes::TEXT, 'none') USING ERRCODE = '22023';
    END IF;
    UPDATE public.genome_annotation_builds
       SET status = 'ready', file_sha256 = p_file_sha256, file_bytes = p_file_bytes,
           gene_count = p_gene_count, term_count = p_term_count, ready_at = now()
     WHERE id = p_build_id;
    RETURN v_row.build;
END;
$$;

-- Marks the caller's unfinished build abandoned; returns whether it changed. A file it left
-- stays in storage until an admin clears it.
CREATE OR REPLACE FUNCTION public.abandon_genome_annotation_build(p_build_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_annotation_builds%ROWTYPE := public._own_annotation_build(p_build_id);
BEGIN
    IF v_row.status = 'abandoned' THEN
        RETURN false;
    END IF;
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'annotation build % is %, not uploading', p_build_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
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

-- True for the file of a build the caller started that is still uploading.
CREATE OR REPLACE FUNCTION public.may_upload_annotation_file(p_name TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.genome_annotation_builds b
     WHERE b.status = 'uploading' AND b.created_by = auth.uid() AND b.file_path = p_name
  );
$$;

REVOKE EXECUTE ON FUNCTION public.start_genome_annotation_build(BIGINT, TEXT, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public._storage_object_bytes(TEXT, TEXT) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public._own_annotation_build(BIGINT) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.finish_genome_annotation_build(BIGINT, TEXT, BIGINT, INTEGER, BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.abandon_genome_annotation_build(BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.withdraw_genome_annotation_build(BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.may_upload_annotation_file(TEXT) FROM PUBLIC, anon, authenticated;

-- Annotations come from developer tools (bloomctl, as a writer) or the pipeline.
GRANT EXECUTE ON FUNCTION public.start_genome_annotation_build(BIGINT, TEXT, JSONB)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.finish_genome_annotation_build(BIGINT, TEXT, BIGINT, INTEGER, BIGINT)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.abandon_genome_annotation_build(BIGINT)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.withdraw_genome_annotation_build(BIGINT, TEXT) TO bloom_admin;
GRANT EXECUTE ON FUNCTION public.may_upload_annotation_file(TEXT) TO bloom_writer, bloom_workflows;

-- 2. Storage -------------------------------------------------------------------------------

-- bloom_writer may add the two files of a genome version it started that is still uploading
-- (as before), or the file of an annotation build it started that is still uploading.
DROP POLICY IF EXISTS writer_insert_own_genome_upload ON storage.objects;
CREATE POLICY writer_insert_own_genome_upload ON storage.objects
    AS RESTRICTIVE
    FOR INSERT TO bloom_writer
    WITH CHECK (
        bucket_id <> 'genome-references'
        OR EXISTS (
            SELECT 1 FROM public.genome_reference_versions v
            WHERE v.status = 'uploading'
              AND v.created_by = auth.uid()
              AND objects.name IN (v.fasta_path, v.gtf_path)
        )
        OR public.may_upload_annotation_file(objects.name)
    );

-- The pipeline adds only its own uploading build's file; it has no other write rule here.
DROP POLICY IF EXISTS workflows_insert_own_annotation_file ON storage.objects;
CREATE POLICY workflows_insert_own_annotation_file ON storage.objects
    FOR INSERT TO bloom_workflows
    WITH CHECK (bucket_id = 'genome-references' AND public.may_upload_annotation_file(name));

-- A ready or withdrawn build's file stays as it is, for bloom_admin too.
DROP POLICY IF EXISTS admin_keep_finished_annotation_files_update ON storage.objects;
CREATE POLICY admin_keep_finished_annotation_files_update ON storage.objects
    AS RESTRICTIVE
    FOR UPDATE TO bloom_admin
    USING (
        bucket_id <> 'genome-references'
        OR NOT EXISTS (
            SELECT 1 FROM public.genome_annotation_builds b
            WHERE b.status IN ('ready', 'withdrawn') AND objects.name = b.file_path
        )
    );

DROP POLICY IF EXISTS admin_keep_finished_annotation_files_delete ON storage.objects;
CREATE POLICY admin_keep_finished_annotation_files_delete ON storage.objects
    AS RESTRICTIVE
    FOR DELETE TO bloom_admin
    USING (
        bucket_id <> 'genome-references'
        OR NOT EXISTS (
            SELECT 1 FROM public.genome_annotation_builds b
            WHERE b.status IN ('ready', 'withdrawn') AND objects.name = b.file_path
        )
    );

COMMIT;
