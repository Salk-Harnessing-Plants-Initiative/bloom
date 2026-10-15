-- Rollback for 20261016100100_add_genome_annotation_uploads.sql
-- Manual break-glass only; nothing runs it automatically. Drops the annotation upload
-- functions. Run it after the rollbacks of 20261016100300 and 20261016100200, and before the
-- rollback of 20261016100000.

BEGIN;

DROP FUNCTION IF EXISTS public.withdraw_genome_annotation_build(BIGINT, TEXT);
DROP FUNCTION IF EXISTS public.abandon_genome_annotation_build(BIGINT);
DROP FUNCTION IF EXISTS public.finish_genome_annotation_build(BIGINT, INTEGER, BIGINT);
DROP FUNCTION IF EXISTS public.add_genome_gene_descriptions(BIGINT, JSONB);
DROP FUNCTION IF EXISTS public.add_genome_gene_terms(BIGINT, JSONB);
DROP FUNCTION IF EXISTS public._own_uploading_annotation_build(BIGINT);
DROP FUNCTION IF EXISTS public.add_functional_terms(JSONB);
DROP FUNCTION IF EXISTS public.start_genome_annotation_build(BIGINT, TEXT, JSONB);
DROP FUNCTION IF EXISTS public.genome_annotation_source_kinds(TEXT);

COMMIT;
