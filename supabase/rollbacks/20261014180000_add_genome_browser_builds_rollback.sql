-- Rollback for 20261014180000_add_genome_browser_builds.sql
-- Manual break-glass only; nothing runs it automatically. Drops the browser build tables (which
-- erases every gene position), the gene positions view and their trigger functions. Run the
-- rollback of 20261014180100 first.

BEGIN;

DROP VIEW IF EXISTS public.genome_gene_positions;
DROP TABLE IF EXISTS public.genome_genes;
DROP TABLE IF EXISTS public.genome_browser_objects;
DROP TABLE IF EXISTS public.genome_browser_builds;

DROP FUNCTION IF EXISTS public.genome_browser_rows_guard();
DROP FUNCTION IF EXISTS public.genome_browser_builds_guard();
DROP FUNCTION IF EXISTS public.genome_browser_builds_number();

COMMIT;
