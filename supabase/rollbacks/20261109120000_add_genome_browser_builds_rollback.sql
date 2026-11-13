-- Rollback for 20261109120000_add_genome_browser_builds.sql
-- Manual break-glass only; nothing runs it automatically. Drops genome_browser_builds and
-- genome_browser_genes, which forgets every browser build and its gene positions; delete the files under
-- <genome>/v<N>/browser/ in genome-references through the Storage API. Run the rollback of
-- 20261109120100 first.

BEGIN;

DROP TABLE IF EXISTS public.genome_browser_genes;
DROP FUNCTION IF EXISTS public.genome_browser_genes_guard();
DROP TABLE IF EXISTS public.genome_browser_builds;
DROP FUNCTION IF EXISTS public.genome_browser_builds_guard();
DROP FUNCTION IF EXISTS public.genome_browser_builds_number();
DROP FUNCTION IF EXISTS public.genome_browser_files_ok(JSONB);
DROP FUNCTION IF EXISTS public.genome_browser_build_roles();

COMMIT;
