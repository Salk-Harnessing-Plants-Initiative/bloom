-- Rollback for 20261016100300_add_scrna_enrichment_uploads.sql
-- Manual break-glass only; nothing runs it automatically. Drops the dataset link and
-- enrichment upload functions and the enrichment storage rules. Run it before the rollback of
-- 20261016100200.

BEGIN;

DROP POLICY IF EXISTS admin_keep_finished_enrichment_files_delete ON storage.objects;
DROP POLICY IF EXISTS admin_keep_finished_enrichment_files_update ON storage.objects;
DROP POLICY IF EXISTS workflows_insert_own_enrichment_file ON storage.objects;
DROP POLICY IF EXISTS writer_no_enrichment_update ON storage.objects;
DROP POLICY IF EXISTS writer_insert_own_enrichment_file ON storage.objects;

DROP FUNCTION IF EXISTS public.may_upload_enrichment_file(TEXT);
DROP FUNCTION IF EXISTS public.abandon_scrna_enrichment_run(BIGINT);
DROP FUNCTION IF EXISTS public.finish_scrna_enrichment_run(BIGINT, TEXT, BIGINT, INTEGER);
DROP FUNCTION IF EXISTS public._own_enrichment_run(BIGINT);
DROP FUNCTION IF EXISTS public.start_scrna_enrichment_run(BIGINT, BIGINT, BIGINT, JSONB, BIGINT);
DROP FUNCTION IF EXISTS public.link_scrna_dataset_genome(BIGINT, BIGINT);

COMMIT;
