-- 20261014180100_add_genome_browser_build_uploads.sql
--
-- Building a genome version's browser files. A writer builds them on their own machine, starts
-- a build, uploads its five files to the genome-references bucket, adds its genes and finishes
-- it, which makes it ready; a build that stops is abandoned. An admin withdraws a ready build.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- 1. Build functions ----------------------------------------------------------------------

-- Starts the next browser build of a ready genome version for the signed-in user. Returns the
-- build and the five object names to upload to.
CREATE OR REPLACE FUNCTION public.start_genome_browser_build(
    p_version_id BIGINT,
    p_tool_versions JSONB DEFAULT '{}'::jsonb
) RETURNS TABLE(
    build_id BIGINT, build INTEGER, fasta_path TEXT, fai_path TEXT, gzi_path TEXT,
    gff_path TEXT, tbi_path TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict use_column
DECLARE
    v_user UUID := auth.uid();
    v_live public.genome_browser_builds%ROWTYPE;
    v_row public.genome_browser_builds%ROWTYPE;
    v_folder TEXT;
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'sign in to build genome browser files' USING ERRCODE = '42501';
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

    -- genome_browser_builds_number checks the version is ready and sets the build number.
    INSERT INTO public.genome_browser_builds (version_id, build, tool_versions, created_by)
    VALUES (p_version_id, 0, p_tool_versions, v_user)
    RETURNING * INTO v_row;

    SELECT g.name || '/v' || v.version || '/browser/b' || v_row.build || '/' INTO v_folder
      FROM public.genome_reference_versions v
      JOIN public.genome_references g ON g.id = v.genome_id
     WHERE v.id = p_version_id;

    INSERT INTO public.genome_browser_objects (build_id, kind, path)
    SELECT v_row.id, f.kind, v_folder || f.file
      FROM (VALUES ('fasta', 'genome.fa.bgz'), ('fai', 'genome.fa.bgz.fai'),
                   ('gzi', 'genome.fa.bgz.gzi'), ('gff', 'genes.gff3.bgz'),
                   ('tbi', 'genes.gff3.bgz.tbi')) AS f(kind, file);

    RETURN QUERY SELECT v_row.id, v_row.build,
        v_folder || 'genome.fa.bgz', v_folder || 'genome.fa.bgz.fai',
        v_folder || 'genome.fa.bgz.gzi', v_folder || 'genes.gff3.bgz',
        v_folder || 'genes.gff3.bgz.tbi';
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
    v_user UUID := auth.uid();
    v_row public.genome_browser_builds%ROWTYPE;
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'sign in to build genome browser files' USING ERRCODE = '42501';
    END IF;
    SELECT * INTO v_row FROM public.genome_browser_builds b WHERE b.id = p_build_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no browser build %', p_build_id USING ERRCODE = '22023';
    END IF;
    IF v_row.created_by <> v_user THEN
        RAISE EXCEPTION 'browser build % was started by someone else', p_build_id
            USING ERRCODE = '42501';
    END IF;
    RETURN v_row;
END;
$$;

-- Adds up to 5,000 genes to the signed-in user's uploading build; returns how many.
CREATE OR REPLACE FUNCTION public.add_genome_genes(p_build_id BIGINT, p_genes JSONB)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_browser_builds%ROWTYPE := public._own_browser_build(p_build_id);
    v_count INTEGER;
BEGIN
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'browser build % is %, not uploading', p_build_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    IF p_genes IS NULL OR jsonb_typeof(p_genes) <> 'array'
       OR jsonb_array_length(p_genes) NOT BETWEEN 1 AND 5000 THEN
        RAISE EXCEPTION 'send 1 to 5000 genes at a time, as a JSON array' USING ERRCODE = '22023';
    END IF;

    INSERT INTO public.genome_genes (
        build_id, gene_id, gene_name, seqid, gene_start, gene_end, strand
    )
    SELECT p_build_id, r.gene_id, r.gene_name, r.seqid, r.gene_start, r.gene_end, r.strand
      FROM jsonb_to_recordset(p_genes) AS r(
          gene_id TEXT, gene_name TEXT, seqid TEXT, gene_start BIGINT, gene_end BIGINT,
          strand TEXT
      );
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

-- Finishes the signed-in user's build: each of the five files must be in storage with
-- exactly the stated size, and the build must hold exactly p_gene_count genes. p_objects is
-- {"fasta": {"sha256": ..., "bytes": ...}, "fai": ..., "gzi": ..., "gff": ..., "tbi": ...}.
-- Makes the build ready; returns its number.
CREATE OR REPLACE FUNCTION public.finish_genome_browser_build(
    p_build_id BIGINT,
    p_objects JSONB,
    p_gene_count INTEGER
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_row public.genome_browser_builds%ROWTYPE := public._own_browser_build(p_build_id);
    v_object public.genome_browser_objects%ROWTYPE;
    v_given JSONB;
    v_sha TEXT;
    v_bytes BIGINT;
    v_stored BIGINT;
    v_genes INTEGER;
BEGIN
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'browser build % is %, not uploading', p_build_id, v_row.status
            USING ERRCODE = '55000';
    END IF;
    IF p_objects IS NULL OR jsonb_typeof(p_objects) <> 'object' THEN
        RAISE EXCEPTION 'files must be a JSON object keyed by kind' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_object_keys(p_objects) k
        WHERE k NOT IN ('fasta', 'fai', 'gzi', 'gff', 'tbi')
    ) THEN
        RAISE EXCEPTION 'file kinds are fasta, fai, gzi, gff and tbi' USING ERRCODE = '22023';
    END IF;

    FOR v_object IN
        SELECT * FROM public.genome_browser_objects o WHERE o.build_id = p_build_id ORDER BY o.kind
    LOOP
        v_given := p_objects -> v_object.kind;
        IF v_given IS NULL OR jsonb_typeof(v_given) <> 'object' THEN
            RAISE EXCEPTION 'no checksum and size given for %', v_object.kind
                USING ERRCODE = '22023';
        END IF;
        v_sha := v_given ->> 'sha256';
        IF v_sha IS NULL OR v_sha !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'the % checksum must be 64 lowercase hex characters', v_object.kind
                USING ERRCODE = '22023';
        END IF;
        IF jsonb_typeof(v_given -> 'bytes') IS DISTINCT FROM 'number' THEN
            RAISE EXCEPTION 'the % size must be a number', v_object.kind USING ERRCODE = '22023';
        END IF;
        v_bytes := (v_given ->> 'bytes')::BIGINT;
        IF v_bytes < 1 THEN
            RAISE EXCEPTION 'the % size must be positive', v_object.kind USING ERRCODE = '22023';
        END IF;
        v_stored := public._genome_object_bytes(v_object.path);
        IF v_stored IS NULL THEN
            RAISE EXCEPTION '% has not been uploaded', v_object.path USING ERRCODE = '22023';
        ELSIF v_stored <> v_bytes THEN
            RAISE EXCEPTION '% is % bytes in storage, not %', v_object.path, v_stored, v_bytes
                USING ERRCODE = '22023';
        END IF;
        UPDATE public.genome_browser_objects
           SET sha256 = v_sha, bytes = v_bytes
         WHERE build_id = p_build_id AND kind = v_object.kind;
    END LOOP;

    SELECT count(*) INTO v_genes FROM public.genome_genes g WHERE g.build_id = p_build_id;
    IF p_gene_count IS NULL OR p_gene_count < 1 OR v_genes <> p_gene_count THEN
        RAISE EXCEPTION 'browser build % holds % genes, not %', p_build_id, v_genes,
            coalesce(p_gene_count::TEXT, 'none') USING ERRCODE = '22023';
    END IF;

    UPDATE public.genome_browser_builds
       SET status = 'ready', gene_count = v_genes, ready_at = now()
     WHERE id = p_build_id;
    RETURN v_row.build;
END;
$$;

-- Marks the signed-in user's unfinished build abandoned and removes its genes; returns
-- whether it changed.
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
    DELETE FROM public.genome_genes WHERE build_id = p_build_id;
    UPDATE public.genome_browser_builds SET status = 'abandoned' WHERE id = p_build_id;
    RETURN true;
END;
$$;

-- Withdraws a ready build, with a reason, so a new build of its version can be made. Runs
-- with the caller's rights, so only bloom_admin can change the row.
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

REVOKE EXECUTE ON FUNCTION public._own_browser_build(BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.start_genome_browser_build(BIGINT, JSONB)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.add_genome_genes(BIGINT, JSONB) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.finish_genome_browser_build(BIGINT, JSONB, INTEGER)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.abandon_genome_browser_build(BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.withdraw_genome_browser_build(BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;

-- bloomctl signs in as bloom_writer; withdrawing is for bloom_admin.
GRANT EXECUTE ON FUNCTION public.start_genome_browser_build(BIGINT, JSONB) TO bloom_writer;
GRANT EXECUTE ON FUNCTION public.add_genome_genes(BIGINT, JSONB) TO bloom_writer;
GRANT EXECUTE ON FUNCTION public.finish_genome_browser_build(BIGINT, JSONB, INTEGER)
    TO bloom_writer;
GRANT EXECUTE ON FUNCTION public.abandon_genome_browser_build(BIGINT) TO bloom_writer;
GRANT EXECUTE ON FUNCTION public.withdraw_genome_browser_build(BIGINT, TEXT) TO bloom_admin;

-- 2. Storage -------------------------------------------------------------------------------

-- The block-gzipped files and .tbi are application/gzip, the .gzi binary, the .fai text.
UPDATE storage.buckets
   SET allowed_mime_types = ARRAY['application/gzip', 'application/octet-stream', 'text/plain']
 WHERE id = 'genome-references';

-- bloom_writer may add the two files of a version it started that is still uploading (as
-- before), or the five files of a browser build it started that is still uploading.
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
        OR EXISTS (
            SELECT 1 FROM public.genome_browser_objects o
            JOIN public.genome_browser_builds b ON b.id = o.build_id
            WHERE b.status = 'uploading'
              AND b.created_by = auth.uid()
              AND objects.name = o.path
        )
    );

-- A ready or withdrawn build's files stay as they are, for bloom_admin too.
DROP POLICY IF EXISTS admin_keep_finished_browser_files_update ON storage.objects;
CREATE POLICY admin_keep_finished_browser_files_update ON storage.objects
    AS RESTRICTIVE
    FOR UPDATE TO bloom_admin
    USING (
        bucket_id <> 'genome-references'
        OR NOT EXISTS (
            SELECT 1 FROM public.genome_browser_objects o
            JOIN public.genome_browser_builds b ON b.id = o.build_id
            WHERE b.status IN ('ready', 'withdrawn') AND objects.name = o.path
        )
    );

DROP POLICY IF EXISTS admin_keep_finished_browser_files_delete ON storage.objects;
CREATE POLICY admin_keep_finished_browser_files_delete ON storage.objects
    AS RESTRICTIVE
    FOR DELETE TO bloom_admin
    USING (
        bucket_id <> 'genome-references'
        OR NOT EXISTS (
            SELECT 1 FROM public.genome_browser_objects o
            JOIN public.genome_browser_builds b ON b.id = o.build_id
            WHERE b.status IN ('ready', 'withdrawn') AND objects.name = o.path
        )
    );

COMMIT;
