-- Rollback for 20260417000007_scrna_rpcs_gene_search_and_cell_arrays.sql

BEGIN;

DROP FUNCTION IF EXISTS public.scrna_gene_search(BIGINT, TEXT, INT);
DROP FUNCTION IF EXISTS public.scrna_cell_arrays(BIGINT);

COMMIT;
