-- 20261109120100_add_genome_browser_build_uploads.sql
--
-- Making a genome version's browser build. A writer or the pipeline starts a build for a ready
-- version, uploads the five bgzip and index files to the folder it is given, adds the GTF's genes
-- in batches, and finishes the build with each file's SHA-256 and size and the gene count;
-- finishing checks every one of the five is stored at exactly that size and the build holds
-- exactly that many genes, and makes the build ready. A build that stops is abandoned, which
-- removes its genes; an admin
-- withdraws a ready one. The files are made by `bloomctl genome index`. Only the caller's own
-- uploading build's five names may be added, and nobody may replace or delete a ready or
-- withdrawn build's files.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

SET LOCAL lock_timeout = '5s';

-- 1. Build functions -----------------------------------------------------------------------

-- Starts the next browser build of a ready genome version; returns the build and the folder to
-- upload its five files to.
CREATE OR REPLACE FUNCTION public.start_genome_browser_build(
    p_version_id BIGINT,
    p_tool_versions JSONB DEFAULT '{}'::jsonb
) RETURNS TABLE(build_id BIGINT, build INTEGER, folder TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict use_column
DECLARE
    v_user UUID := auth.uid();
    v_live public.genome_browser_builds%ROWTYPE;
    v_row public.genome_browser_builds%ROWTYPE;
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'sign in to build a genome for the browser' USING ERRCODE = '42501';
    END IF;
    IF p_tool_versions IS NULL OR jsonb_typeof(p_tool_versions) <> 'object' THEN
        RAISE EXCEPTION 'tool versions must be a JSON object' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO v_live FROM public.genome_browser_builds b
     WHERE b.version_id = p_version_id AND b.status IN ('uploading', 'ready');
    IF FOUND THEN
        RAISE EXCEPTION 'genome version % already has browser build % (%)',
            p_version_id, v_live.build, v_live.status USING ERRCODE = '55000';
    END IF;
    -- genome_browser_builds_number checks the version is ready and sets the number and folder.
    INSERT INTO public.genome_browser_builds (version_id, build, tool_versions, folder,
                                              created_by)
    VALUES (p_version_id, 0, p_tool_versions, '', v_user)
    RETURNING * INTO v_row;
    RETURN QUERY SELECT v_row.id, v_row.build, v_row.folder;
END;
$$;

-- The signed-in user's build, locked; refuses one started by someone else.
CREATE OR REPLACE FUNCTION public._own_browser_build(p_build_id BIGINT)
RETURNS public.genome_browser_builds
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_browser_builds%ROWTYPE;
BEGIN
    IF auth.uid() IS NULL THEN
        RAISE EXCEPTION 'sign in to build a genome for the browser' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_row FROM public.genome_browser_builds b WHERE b.id = p_build_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no browser build %', p_build_id USING ERRCODE = '22023';
    END IF;
    IF v_row.created_by <> auth.uid() THEN
        RAISE EXCEPTION 'browser build % was started by someone else', p_build_id
            USING ERRCODE = '42501';
    END IF;
    RETURN v_row;
END;
$$;

-- Adds 1 to 5,000 genes to the caller's uploading build; returns how many were new. Each gene
-- is {"gene_id", "gene_name", "contig", "gene_start", "gene_end", "strand"}. Sending a batch
-- again is safe: a gene already added with all the same details is skipped.
CREATE OR REPLACE FUNCTION public.add_genome_browser_genes(p_build_id BIGINT, p_genes JSONB)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_browser_builds%ROWTYPE := public._own_browser_build(p_build_id);
    v_count INTEGER;
    v_gene TEXT;
BEGIN
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'browser build % is %, not uploading', p_build_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    IF p_genes IS NULL OR jsonb_typeof(p_genes) <> 'array'
       OR jsonb_array_length(p_genes) NOT BETWEEN 1 AND 5000 THEN
        RAISE EXCEPTION 'send 1 to 5000 genes at a time, as a JSON array'
            USING ERRCODE = '22023';
    END IF;
    SELECT r.gene_id INTO v_gene
      FROM jsonb_to_recordset(p_genes) AS r(gene_id TEXT)
     WHERE r.gene_id IS NOT NULL GROUP BY r.gene_id HAVING count(*) > 1 ORDER BY r.gene_id LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'gene % appears more than once; a browser build holds one position per gene',
            v_gene USING ERRCODE = '22023';
    END IF;
    SELECT n.gene_id INTO v_gene
      FROM jsonb_to_recordset(p_genes) AS n(
          gene_id TEXT, gene_name TEXT, contig TEXT, gene_start BIGINT, gene_end BIGINT,
          strand TEXT
      )
      JOIN public.genome_browser_genes g
        ON g.browser_build_id = p_build_id AND g.gene_id = n.gene_id
     WHERE (g.gene_name, g.contig, g.gene_start, g.gene_end, g.strand)
           IS DISTINCT FROM (n.gene_name, n.contig, n.gene_start, n.gene_end, n.strand)
     ORDER BY n.gene_id LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'gene % was already added to browser build % with different details',
            v_gene, p_build_id USING ERRCODE = '23505';
    END IF;

    INSERT INTO public.genome_browser_genes (browser_build_id, gene_id, gene_name, contig,
                                             gene_start, gene_end, strand)
    SELECT p_build_id, r.gene_id, r.gene_name, r.contig, r.gene_start, r.gene_end, r.strand
      FROM jsonb_to_recordset(p_genes) AS r(
          gene_id TEXT, gene_name TEXT, contig TEXT, gene_start BIGINT, gene_end BIGINT,
          strand TEXT
      )
    ON CONFLICT (browser_build_id, gene_id) DO NOTHING;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- Finishes the caller's build: each of the five files must be stored under the build's folder
-- at exactly the stated size, and the build must hold exactly p_gene_count genes. Records the
-- files and the gene count; returns the build number.
CREATE OR REPLACE FUNCTION public.finish_genome_browser_build(
    p_build_id BIGINT,
    p_files JSONB,
    p_gene_count INTEGER
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_browser_builds%ROWTYPE := public._own_browser_build(p_build_id);
    v_file RECORD;
    v_name TEXT;
    v_stated BIGINT;
    v_stored BIGINT;
    v_genes INTEGER;
BEGIN
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'browser build % is %, not uploading', p_build_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    IF NOT public.genome_browser_files_ok(p_files) THEN
        RAISE EXCEPTION 'a browser build records exactly the five files, each with a 64 '
                        'lowercase hex sha256 and a positive bytes' USING ERRCODE = '22023';
    END IF;
    FOR v_file IN SELECT r.role, r.basename FROM public.genome_browser_build_roles() r
                   ORDER BY r.role
    LOOP
        v_name := v_row.folder || '/' || v_file.basename;
        v_stated := (p_files -> v_file.role ->> 'bytes')::BIGINT;
        v_stored := public._storage_object_bytes('genome-references', v_name);
        IF v_stored IS NULL THEN
            RAISE EXCEPTION '% has not been uploaded', v_name USING ERRCODE = '22023';
        ELSIF v_stored IS DISTINCT FROM v_stated THEN
            RAISE EXCEPTION '% is % bytes in storage, not %', v_name, v_stored, v_stated
                USING ERRCODE = '22023';
        END IF;
    END LOOP;
    IF p_gene_count IS NULL OR p_gene_count < 1 THEN
        RAISE EXCEPTION 'a browser build records at least one gene' USING ERRCODE = '22023';
    END IF;
    SELECT count(*) INTO v_genes FROM public.genome_browser_genes g WHERE g.browser_build_id = p_build_id;
    IF v_genes <> p_gene_count THEN
        RAISE EXCEPTION 'browser build % holds % genes, not %', p_build_id, v_genes,
            p_gene_count USING ERRCODE = '22023';
    END IF;
    UPDATE public.genome_browser_builds
       SET status = 'ready', files = p_files, gene_count = v_genes, ready_at = now()
     WHERE id = p_build_id;
    RETURN v_row.build;
END;
$$;

-- Marks the caller's unfinished build abandoned and removes its genes; returns whether it
-- changed. Files it left stay in storage until an admin clears them.
CREATE OR REPLACE FUNCTION public.abandon_genome_browser_build(p_build_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_browser_builds%ROWTYPE := public._own_browser_build(p_build_id);
BEGIN
    IF v_row.status = 'abandoned' THEN
        RETURN false;
    END IF;
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'browser build % is %, not uploading', p_build_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    DELETE FROM public.genome_browser_genes WHERE browser_build_id = p_build_id;
    UPDATE public.genome_browser_builds SET status = 'abandoned' WHERE id = p_build_id;
    RETURN true;
END;
$$;

-- Withdraws a ready build, with a reason, so a new build can be made. Runs with the caller's
-- rights, so only bloom_admin can change the row.
CREATE OR REPLACE FUNCTION public.withdraw_genome_browser_build(p_build_id BIGINT, p_reason TEXT)
RETURNS VOID
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    UPDATE public.genome_browser_builds
       SET status = 'withdrawn', withdrawn_reason = p_reason
     WHERE id = p_build_id AND status = 'ready';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no ready browser build %', p_build_id USING ERRCODE = '22023';
    END IF;
END;
$$;

-- True for one of the five files of a build the caller started that is still uploading.
CREATE OR REPLACE FUNCTION public.may_upload_browser_file(p_name TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.genome_browser_builds b, public.genome_browser_build_roles() r
     WHERE b.status = 'uploading' AND b.created_by = auth.uid()
       AND p_name = b.folder || '/' || r.basename
  );
$$;

REVOKE EXECUTE ON FUNCTION public.start_genome_browser_build(BIGINT, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public._own_browser_build(BIGINT) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.add_genome_browser_genes(BIGINT, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.finish_genome_browser_build(BIGINT, JSONB, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.abandon_genome_browser_build(BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.withdraw_genome_browser_build(BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.may_upload_browser_file(TEXT) FROM PUBLIC, anon, authenticated;

-- Builds are made by developer tools (bloomctl, as a writer) or the pipeline.
GRANT EXECUTE ON FUNCTION public.start_genome_browser_build(BIGINT, JSONB)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.add_genome_browser_genes(BIGINT, JSONB)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.finish_genome_browser_build(BIGINT, JSONB, INTEGER)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.abandon_genome_browser_build(BIGINT)
    TO bloom_writer, bloom_workflows;
GRANT EXECUTE ON FUNCTION public.withdraw_genome_browser_build(BIGINT, TEXT) TO bloom_admin;
GRANT EXECUTE ON FUNCTION public.may_upload_browser_file(TEXT) TO bloom_writer, bloom_workflows;

-- 2. Storage -------------------------------------------------------------------------------

-- A .fai is text and a .gzi and .tbi are binary, so the bucket has to take more than gzip.
-- What a writer may put where is set by the policies below, not by this list.
UPDATE storage.buckets
   SET allowed_mime_types = ARRAY['application/gzip', 'application/octet-stream', 'text/plain']
 WHERE id = 'genome-references';

-- bloom_writer may add the two files of a genome version it started that is still uploading, or
-- the file of an annotation build it started, or one of the five files of a browser build it
-- started. Replaces the policy of 20261017100100.
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
        OR public.may_upload_browser_file(objects.name)
    );

-- The pipeline adds only its own uploading build's files.
DROP POLICY IF EXISTS workflows_insert_own_browser_file ON storage.objects;
CREATE POLICY workflows_insert_own_browser_file ON storage.objects
    FOR INSERT TO bloom_workflows
    WITH CHECK (bucket_id = 'genome-references' AND public.may_upload_browser_file(name));

-- A ready or withdrawn build's files stay as they are, for bloom_admin too.
DROP POLICY IF EXISTS admin_keep_finished_browser_files_update ON storage.objects;
CREATE POLICY admin_keep_finished_browser_files_update ON storage.objects
    AS RESTRICTIVE
    FOR UPDATE TO bloom_admin
    USING (
        bucket_id <> 'genome-references'
        OR NOT EXISTS (
            SELECT 1 FROM public.genome_browser_builds b, public.genome_browser_build_roles() r
            WHERE b.status IN ('ready', 'withdrawn')
              AND objects.name = b.folder || '/' || r.basename
        )
    );

DROP POLICY IF EXISTS admin_keep_finished_browser_files_delete ON storage.objects;
CREATE POLICY admin_keep_finished_browser_files_delete ON storage.objects
    AS RESTRICTIVE
    FOR DELETE TO bloom_admin
    USING (
        bucket_id <> 'genome-references'
        OR NOT EXISTS (
            SELECT 1 FROM public.genome_browser_builds b, public.genome_browser_build_roles() r
            WHERE b.status IN ('ready', 'withdrawn')
              AND objects.name = b.folder || '/' || r.basename
        )
    );

-- PostgREST serves RPCs from a cached schema.
NOTIFY pgrst, 'reload schema';

COMMIT;
