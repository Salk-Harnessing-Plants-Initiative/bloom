-- Rollback for 20261016100300_add_scrna_enrichment_uploads.sql
-- Manual break-glass only; nothing runs it automatically. Drops the dataset link and
-- enrichment upload functions. Run it before the rollback of 20261016100200.

BEGIN;

DROP FUNCTION IF EXISTS public.abandon_scrna_enrichment_run(BIGINT);
DROP FUNCTION IF EXISTS public.finish_scrna_enrichment_run(BIGINT, INTEGER);
DROP FUNCTION IF EXISTS public.add_scrna_enrichment_terms(BIGINT, JSONB);
DROP FUNCTION IF EXISTS public._own_uploading_enrichment_run(BIGINT);
DROP FUNCTION IF EXISTS public.start_scrna_enrichment_run(BIGINT, BIGINT, BIGINT, JSONB, BIGINT);
DROP FUNCTION IF EXISTS public.link_scrna_dataset_genome(BIGINT, BIGINT);

COMMIT;
