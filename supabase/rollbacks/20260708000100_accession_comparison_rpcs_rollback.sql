-- Manual rollback for 20260708000100_accession_comparison_rpcs.sql
--
-- Drops the three new accession comparison RPCs and restores search_genes to
-- its pre-scoping body (the unscoped version from 20260610000000, without the
-- accession_id IS NULL filter). Apply this BEFORE the M1 rollback, since
-- search_genes here still references proteins but no longer accession_id.

BEGIN;

DROP FUNCTION IF EXISTS public.knn_search_esm3(text, int);
DROP FUNCTION IF EXISTS public.compare_gene_across_accessions(text, text, int);
DROP FUNCTION IF EXISTS public.search_accession_genes(text, int);

-- Restore the original unscoped search_genes (verbatim from 20260610000000).
CREATE OR REPLACE FUNCTION public.search_genes(
  partial_id  text,
  max_results int DEFAULT 20
)
RETURNS TABLE (
  uid     text,
  species text,
  gene_id text
)
LANGUAGE sql STABLE
AS $$
  SELECT uid, species, gene_id
    FROM public.proteins
   WHERE char_length(btrim(partial_id)) >= 1
     AND ( uid     ILIKE '%' || partial_id || '%'
        OR gene_id ILIKE '%' || partial_id || '%' )
   ORDER BY uid
   LIMIT LEAST(max_results, 1000);
$$;

GRANT EXECUTE ON FUNCTION public.search_genes(text, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
