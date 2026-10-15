-- Rollback for 20261016100000_add_genome_annotations.sql
-- Manual break-glass only; nothing runs it automatically. Drops the annotation tables (which
-- erases every genome annotation and term name). Run the other rollbacks of 20261016100* first.

BEGIN;

DROP TABLE IF EXISTS public.genome_gene_descriptions;
DROP TABLE IF EXISTS public.genome_gene_terms;
DROP TABLE IF EXISTS public.functional_terms;
DROP TABLE IF EXISTS public.genome_annotation_builds;

DROP FUNCTION IF EXISTS public.genome_annotation_rows_guard();
DROP FUNCTION IF EXISTS public.genome_annotation_builds_guard();
DROP FUNCTION IF EXISTS public.genome_annotation_builds_number();

COMMIT;
