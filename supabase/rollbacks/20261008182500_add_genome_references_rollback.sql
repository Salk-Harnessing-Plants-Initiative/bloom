-- Rollback for 20261008182500_add_genome_references.sql
-- Manual break-glass only; nothing runs it automatically. Roll back
-- 20261008182600_add_genome_reference_uploads.sql first. Drops the genome tables, their
-- triggers and the name rule.

BEGIN;

DROP TABLE IF EXISTS public.genome_reference_versions;
DROP TABLE IF EXISTS public.genome_references;
DROP FUNCTION IF EXISTS public.genome_reference_versions_guard();
DROP FUNCTION IF EXISTS public.genome_reference_versions_number();
DROP FUNCTION IF EXISTS public.genome_references_keep_identity();
DROP FUNCTION IF EXISTS public.genome_name_ok(TEXT);

COMMIT;
