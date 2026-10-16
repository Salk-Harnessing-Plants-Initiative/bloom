-- Rollback for 20261016100100_add_genome_annotation_uploads.sql
-- Manual break-glass only; nothing runs it automatically. Drops the annotation upload
-- functions and storage rules, and restores the writer upload rule for genome-references to
-- the genome files only. Run it after the rollbacks of 20261016100300 and 20261016100200, and
-- before the rollback of 20261016100000.

BEGIN;

DROP POLICY IF EXISTS admin_keep_finished_annotation_files_delete ON storage.objects;
DROP POLICY IF EXISTS admin_keep_finished_annotation_files_update ON storage.objects;
DROP POLICY IF EXISTS workflows_insert_own_annotation_file ON storage.objects;

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

DROP FUNCTION IF EXISTS public.may_upload_annotation_file(TEXT);
DROP FUNCTION IF EXISTS public.withdraw_genome_annotation_build(BIGINT, TEXT);
DROP FUNCTION IF EXISTS public.abandon_genome_annotation_build(BIGINT);
DROP FUNCTION IF EXISTS public.finish_genome_annotation_build(BIGINT, TEXT, BIGINT, INTEGER, BIGINT);
DROP FUNCTION IF EXISTS public._own_annotation_build(BIGINT);
DROP FUNCTION IF EXISTS public._storage_object_bytes(TEXT, TEXT);
DROP FUNCTION IF EXISTS public.start_genome_annotation_build(BIGINT, TEXT, JSONB);

COMMIT;
