-- 20261008182600_add_genome_reference_uploads.sql
--
-- Uploading a genome version, and recording the version a run used. A writer starts a version,
-- uploads its two files to the genome-references bucket, and finishes it, which makes it
-- ready; an upload that stops is abandoned. A run's genome_version_id must name a ready
-- version and never changes once set.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- 1. Upload functions ----------------------------------------------------------------------

-- Starts the next version of a genome for the signed-in user, creating the genome when it is
-- new (p_species_id is then required). For an existing genome, a species or description given
-- must match the stored one. Returns the version and the two object names to upload to.
CREATE OR REPLACE FUNCTION public.start_genome_version(
    p_genome TEXT,
    p_species_id BIGINT DEFAULT NULL,
    p_description TEXT DEFAULT NULL,
    p_assembly TEXT DEFAULT NULL,
    p_annotation TEXT DEFAULT NULL,
    p_source_url TEXT DEFAULT NULL,
    p_notes TEXT DEFAULT NULL
) RETURNS TABLE(version_id BIGINT, version INTEGER, fasta_path TEXT, gtf_path TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
#variable_conflict use_column
DECLARE
    v_user UUID := auth.uid();
    v_genome public.genome_references%ROWTYPE;
    v_row public.genome_reference_versions%ROWTYPE;
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'sign in to upload a genome' USING ERRCODE = '42501';
    END IF;
    IF p_genome IS NULL OR NOT public.genome_name_ok(p_genome) THEN
        RAISE EXCEPTION 'invalid genome name %: use 1 to 64 lowercase letters, digits, ''.'', '
            '''_'' or ''-'', starting with a letter or digit, with no ''__'' and not ending in '
            '''.v'' and a number', p_genome USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_genome FROM public.genome_references g WHERE g.name = p_genome;
    IF NOT FOUND THEN
        IF p_species_id IS NULL THEN
            RAISE EXCEPTION 'genome % is new; give its species', p_genome USING ERRCODE = '22023';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.species s WHERE s.id = p_species_id AND s.deleted_at IS NULL
        ) THEN
            RAISE EXCEPTION 'no species with id % (it may have been deleted)', p_species_id
                USING ERRCODE = '22023';
        END IF;
        -- Another upload may create the same genome first; then this one uses it.
        INSERT INTO public.genome_references (name, species_id, description, created_by)
        VALUES (p_genome, p_species_id, p_description, v_user)
        ON CONFLICT (name) DO NOTHING;
    END IF;

    -- The row lock numbers concurrent uploads of one genome one after another.
    SELECT * INTO v_genome FROM public.genome_references g WHERE g.name = p_genome FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'genome % was removed while starting the upload', p_genome
            USING ERRCODE = '55000';
    END IF;
    IF p_species_id IS NOT NULL AND p_species_id <> v_genome.species_id THEN
        RAISE EXCEPTION 'genome % belongs to species %, not %',
            p_genome, v_genome.species_id, p_species_id USING ERRCODE = '22023';
    END IF;
    IF p_description IS NOT NULL AND p_description IS DISTINCT FROM v_genome.description THEN
        RAISE EXCEPTION 'genome % already has the description %', p_genome,
            coalesce(quote_literal(v_genome.description), 'none') USING ERRCODE = '22023';
    END IF;

    -- genome_reference_versions_number sets the version number and the paths.
    INSERT INTO public.genome_reference_versions (
        genome_id, version, fasta_path, gtf_path, assembly, annotation, source_url, notes,
        created_by
    ) VALUES (
        v_genome.id, 0, '', '', p_assembly, p_annotation, p_source_url, p_notes, v_user
    )
    RETURNING * INTO v_row;

    RETURN QUERY SELECT v_row.id, v_row.version, v_row.fasta_path, v_row.gtf_path;
END;
$$;

-- Size of one stored object in the genome-references bucket, or NULL when it is not there.
CREATE OR REPLACE FUNCTION public._genome_object_bytes(p_name TEXT)
RETURNS BIGINT
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
    SELECT (o.metadata ->> 'size')::BIGINT
    FROM storage.objects o
    WHERE o.bucket_id = 'genome-references' AND o.name = p_name;
$$;

-- Finishes the signed-in user's upload: both files must be recorded in storage with exactly
-- the stated sizes. The checksums are the uploader's; the pipeline checks them when it
-- downloads. Makes the version ready; returns its version number.
CREATE OR REPLACE FUNCTION public.finish_genome_version(
    p_version_id BIGINT,
    p_fasta_sha256 TEXT,
    p_fasta_bytes BIGINT,
    p_gtf_sha256 TEXT,
    p_gtf_bytes BIGINT
) RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_user UUID := auth.uid();
    v_row public.genome_reference_versions%ROWTYPE;
    v_stored BIGINT;
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'sign in to upload a genome' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_row FROM public.genome_reference_versions v
    WHERE v.id = p_version_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no genome version %', p_version_id USING ERRCODE = '22023';
    END IF;
    IF v_row.created_by <> v_user THEN
        RAISE EXCEPTION 'genome version % was started by someone else', p_version_id
            USING ERRCODE = '42501';
    END IF;
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'genome version % is %, not uploading', p_version_id, v_row.status
            USING ERRCODE = '55000';
    END IF;

    IF p_fasta_sha256 IS NULL OR p_fasta_sha256 !~ '^[0-9a-f]{64}$'
       OR p_gtf_sha256 IS NULL OR p_gtf_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'checksums must be 64 lowercase hex characters' USING ERRCODE = '22023';
    END IF;
    IF p_fasta_bytes IS NULL OR p_fasta_bytes < 1 OR p_gtf_bytes IS NULL OR p_gtf_bytes < 1 THEN
        RAISE EXCEPTION 'sizes must be positive' USING ERRCODE = '22023';
    END IF;

    v_stored := public._genome_object_bytes(v_row.fasta_path);
    IF v_stored IS NULL THEN
        RAISE EXCEPTION '% has not been uploaded', v_row.fasta_path USING ERRCODE = '22023';
    ELSIF v_stored <> p_fasta_bytes THEN
        RAISE EXCEPTION '% is % bytes in storage, not %', v_row.fasta_path, v_stored, p_fasta_bytes
            USING ERRCODE = '22023';
    END IF;
    v_stored := public._genome_object_bytes(v_row.gtf_path);
    IF v_stored IS NULL THEN
        RAISE EXCEPTION '% has not been uploaded', v_row.gtf_path USING ERRCODE = '22023';
    ELSIF v_stored <> p_gtf_bytes THEN
        RAISE EXCEPTION '% is % bytes in storage, not %', v_row.gtf_path, v_stored, p_gtf_bytes
            USING ERRCODE = '22023';
    END IF;

    UPDATE public.genome_reference_versions
    SET status = 'ready',
        fasta_sha256 = p_fasta_sha256,
        fasta_bytes = p_fasta_bytes,
        gtf_sha256 = p_gtf_sha256,
        gtf_bytes = p_gtf_bytes,
        ready_at = now()
    WHERE id = v_row.id;

    RETURN v_row.version;
END;
$$;

-- Marks the signed-in user's unfinished upload abandoned; returns whether it changed.
CREATE OR REPLACE FUNCTION public.abandon_genome_version(p_version_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_user UUID := auth.uid();
    v_row public.genome_reference_versions%ROWTYPE;
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'sign in to upload a genome' USING ERRCODE = '42501';
    END IF;

    SELECT * INTO v_row FROM public.genome_reference_versions v
    WHERE v.id = p_version_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no genome version %', p_version_id USING ERRCODE = '22023';
    END IF;
    IF v_row.created_by <> v_user THEN
        RAISE EXCEPTION 'genome version % was started by someone else', p_version_id
            USING ERRCODE = '42501';
    END IF;
    IF v_row.status = 'abandoned' THEN
        RETURN false;
    END IF;
    IF v_row.status <> 'uploading' THEN
        RAISE EXCEPTION 'genome version % is %, not uploading', p_version_id, v_row.status
            USING ERRCODE = '55000';
    END IF;

    UPDATE public.genome_reference_versions SET status = 'abandoned' WHERE id = v_row.id;
    RETURN true;
END;
$$;

REVOKE EXECUTE ON FUNCTION public._genome_object_bytes(TEXT) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.start_genome_version(TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.finish_genome_version(BIGINT, TEXT, BIGINT, TEXT, BIGINT)
    FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.abandon_genome_version(BIGINT)
    FROM PUBLIC, anon, authenticated;

-- bloomctl signs in as bloom_writer.
GRANT EXECUTE ON FUNCTION public.start_genome_version(TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)
    TO bloom_writer;
GRANT EXECUTE ON FUNCTION public.finish_genome_version(BIGINT, TEXT, BIGINT, TEXT, BIGINT)
    TO bloom_writer;
GRANT EXECUTE ON FUNCTION public.abandon_genome_version(BIGINT) TO bloom_writer;

-- 2. The genome-references bucket ----------------------------------------------------------

-- Gzipped FASTA and GTF files of at most 500 MB, Storage's own per-object limit.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
  VALUES ('genome-references', 'genome-references', false, 524288000,
          ARRAY['application/gzip'])
    ON CONFLICT (id) DO UPDATE
      SET public = false,
          file_size_limit = EXCLUDED.file_size_limit,
          allowed_mime_types = EXCLUDED.allowed_mime_types;

-- bloom_admin: full access, except changing a finished version's files (below)
DROP POLICY IF EXISTS admin_all_genome_references_objects ON storage.objects;
CREATE POLICY admin_all_genome_references_objects ON storage.objects
    FOR ALL TO bloom_admin
    USING (bucket_id = 'genome-references')
    WITH CHECK (bucket_id = 'genome-references');

-- bloom_agent, bloom_user and bloom_workflows: read-only; the pipeline downloads with it
DROP POLICY IF EXISTS agent_read_genome_references_objects ON storage.objects;
CREATE POLICY agent_read_genome_references_objects ON storage.objects
    FOR SELECT TO bloom_agent
    USING (bucket_id = 'genome-references');

DROP POLICY IF EXISTS user_read_genome_references_objects ON storage.objects;
CREATE POLICY user_read_genome_references_objects ON storage.objects
    FOR SELECT TO bloom_user
    USING (bucket_id = 'genome-references');

DROP POLICY IF EXISTS workflows_read_genome_references_objects ON storage.objects;
CREATE POLICY workflows_read_genome_references_objects ON storage.objects
    FOR SELECT TO bloom_workflows
    USING (bucket_id = 'genome-references');

-- bloom_writer's blanket INSERT and UPDATE would let it write anywhere in this bucket. These
-- restrictive policies narrow it here only: it may add the two files of a version it started
-- that is still uploading, and may never overwrite, rename or move a file into this bucket.
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
    );

DROP POLICY IF EXISTS writer_no_genome_update ON storage.objects;
CREATE POLICY writer_no_genome_update ON storage.objects
    AS RESTRICTIVE
    FOR UPDATE TO bloom_writer
    USING (bucket_id <> 'genome-references')
    WITH CHECK (bucket_id <> 'genome-references');

-- A ready or withdrawn version's files stay as they are, for bloom_admin too.
DROP POLICY IF EXISTS admin_keep_finished_genome_files_update ON storage.objects;
CREATE POLICY admin_keep_finished_genome_files_update ON storage.objects
    AS RESTRICTIVE
    FOR UPDATE TO bloom_admin
    USING (
        bucket_id <> 'genome-references'
        OR NOT EXISTS (
            SELECT 1 FROM public.genome_reference_versions v
            WHERE v.status IN ('ready', 'withdrawn')
              AND objects.name IN (v.fasta_path, v.gtf_path)
        )
    );

DROP POLICY IF EXISTS admin_keep_finished_genome_files_delete ON storage.objects;
CREATE POLICY admin_keep_finished_genome_files_delete ON storage.objects
    AS RESTRICTIVE
    FOR DELETE TO bloom_admin
    USING (
        bucket_id <> 'genome-references'
        OR NOT EXISTS (
            SELECT 1 FROM public.genome_reference_versions v
            WHERE v.status IN ('ready', 'withdrawn')
              AND objects.name IN (v.fasta_path, v.gtf_path)
        )
    );

-- 3. The genome version a run used ---------------------------------------------------------

-- Empty for runs started before genome versions.
ALTER TABLE public.rnaseq_runs ADD COLUMN IF NOT EXISTS genome_version_id BIGINT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'rnaseq_runs_genome_version_id_fkey'
      AND conrelid = 'public.rnaseq_runs'::regclass
  ) THEN
    ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_genome_version_id_fkey
      FOREIGN KEY (genome_version_id) REFERENCES public.genome_reference_versions(id);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS rnaseq_runs_genome_version_id_idx
    ON public.rnaseq_runs (genome_version_id);

-- A run's genome version must be ready when it is set, and never changes or clears after.
CREATE OR REPLACE FUNCTION public.rnaseq_runs_keep_genome_version()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.genome_version_id IS NOT NULL THEN
        IF NEW.genome_version_id IS DISTINCT FROM OLD.genome_version_id THEN
            RAISE EXCEPTION 'run % already records genome version %; it cannot change',
                OLD.id, OLD.genome_version_id USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.genome_version_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM public.genome_reference_versions v
        WHERE v.id = NEW.genome_version_id AND v.status = 'ready'
    ) THEN
        RAISE EXCEPTION 'genome version % is not ready', NEW.genome_version_id
            USING ERRCODE = '22023';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.rnaseq_runs_keep_genome_version()
    FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS rnaseq_runs_keep_genome_version ON public.rnaseq_runs;
CREATE TRIGGER rnaseq_runs_keep_genome_version
    BEFORE INSERT OR UPDATE OF genome_version_id ON public.rnaseq_runs
    FOR EACH ROW EXECUTE FUNCTION public.rnaseq_runs_keep_genome_version();

COMMIT;
