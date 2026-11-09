-- Rollback for 20261109120100_add_genome_browser_build_uploads.sql
-- Manual break-glass only; nothing runs it automatically. Drops the browser build functions and
-- storage rules, restores the writer upload rule for genome-references to the genome and
-- annotation files, and narrows the bucket's mime list again. Run it before the rollback of
-- 20261109120000.

BEGIN;

DROP POLICY IF EXISTS admin_keep_finished_browser_files_delete ON storage.objects;
DROP POLICY IF EXISTS admin_keep_finished_browser_files_update ON storage.objects;
DROP POLICY IF EXISTS workflows_insert_own_browser_file ON storage.objects;

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

-- Back to gzip only. A .fai, .gzi or .tbi already in the bucket stays; the list is checked on
-- upload, so narrowing it refuses new ones rather than removing old ones.
UPDATE storage.buckets
   SET allowed_mime_types = ARRAY['application/gzip']
 WHERE id = 'genome-references';

DROP FUNCTION IF EXISTS public.may_upload_browser_file(TEXT);
DROP FUNCTION IF EXISTS public.withdraw_genome_browser_build(BIGINT, TEXT);
DROP FUNCTION IF EXISTS public.abandon_genome_browser_build(BIGINT);
DROP FUNCTION IF EXISTS public.finish_genome_browser_build(BIGINT, JSONB, BOOLEAN, INTEGER);
DROP FUNCTION IF EXISTS public.add_genome_genes(BIGINT, JSONB);
DROP FUNCTION IF EXISTS public._own_browser_build(BIGINT);
DROP FUNCTION IF EXISTS public.start_genome_browser_build(BIGINT, JSONB);

NOTIFY pgrst, 'reload schema';

COMMIT;
