-- Rollback for 20261016100000_add_genome_annotations.sql
-- Manual break-glass only; nothing runs it automatically. Drops genome_annotation_builds (which
-- forgets every annotation file; delete them under <genome>/v<N>/annotation/ in
-- genome-references through the Storage API). Run the other rollbacks of 20261016100* first.

BEGIN;

DROP TABLE IF EXISTS public.genome_annotation_builds;
DROP FUNCTION IF EXISTS public.genome_annotation_builds_guard();
DROP FUNCTION IF EXISTS public.genome_annotation_builds_number();

COMMIT;
