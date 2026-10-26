-- Rollback for 20261014180100_add_genome_browser_build_uploads.sql
-- Manual break-glass only; nothing runs it automatically. Drops the browser build functions and
-- the browser storage policies, and restores the bucket's types and the writer upload policy to
-- the genome files only. Run it before the rollback of 20261014180000.
--
-- Refuses while the bucket holds any browser file: they must be deleted through the Storage API
-- first so their bytes don't stay behind in MinIO. SET LOCAL lifts Storage's guard against
-- direct deletes (storage.allow_delete_query) for this transaction only.

BEGIN;

SET LOCAL storage.allow_delete_query = 'true';

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM storage.objects
    WHERE bucket_id = 'genome-references' AND name LIKE '%/browser/%'
  ) THEN
    RAISE EXCEPTION
      'genome-references holds browser files — refusing to drop their records. '
      'Delete them via the Storage API first, then re-run this rollback.';
  END IF;
END
$$;

DROP POLICY IF EXISTS admin_keep_finished_browser_files_update ON storage.objects;
DROP POLICY IF EXISTS admin_keep_finished_browser_files_delete ON storage.objects;

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

UPDATE storage.buckets SET allowed_mime_types = ARRAY['application/gzip']
 WHERE id = 'genome-references';

DROP FUNCTION IF EXISTS public.withdraw_genome_browser_build(BIGINT, TEXT);
DROP FUNCTION IF EXISTS public.abandon_genome_browser_build(BIGINT);
DROP FUNCTION IF EXISTS public.finish_genome_browser_build(BIGINT, JSONB, INTEGER);
DROP FUNCTION IF EXISTS public.add_genome_genes(BIGINT, JSONB);
DROP FUNCTION IF EXISTS public.start_genome_browser_build(BIGINT, JSONB);
DROP FUNCTION IF EXISTS public._own_browser_build(BIGINT);

COMMIT;
