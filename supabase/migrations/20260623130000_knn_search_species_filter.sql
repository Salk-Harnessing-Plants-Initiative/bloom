-- =============================================================================
-- 20260623130000_knn_search_species_filter.sql
--
-- Adds optional species filtering to knn_search_esm2 so the Orthologs UI
-- can return the nearest K proteins WITHIN a chosen set of species (e.g. "the
-- nearest rice orthologs"), rather than only the global top-K which is often
-- dominated by the query's own species.
-- =============================================================================

BEGIN;

-- ─── 1. knn_search_esm2 with optional species filter ──────────────────────
DROP FUNCTION IF EXISTS public.knn_search_esm2(text, int);

CREATE OR REPLACE FUNCTION public.knn_search_esm2(
  query_uid      text,
  match_count    int      DEFAULT 20,
  species_filter text[]   DEFAULT NULL
)
RETURNS TABLE (
  uid        text,
  species    text,
  gene_id    text,
  similarity float
)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
  query_vec vector(1280);
BEGIN
  SELECT embedding INTO query_vec
    FROM public.protein_embeddings_esm2
   WHERE protein_embeddings_esm2.uid = query_uid;

  IF query_vec IS NULL THEN
    RETURN;
  END IF;
  RETURN QUERY
    SELECT p.uid,
           p.species,
           p.gene_id,
           (1 - (e.embedding <=> query_vec))::float AS similarity
      FROM public.protein_embeddings_esm2 e
      JOIN public.proteins p USING (uid)
     WHERE species_filter IS NULL
        OR cardinality(species_filter) = 0
        OR p.species = ANY(species_filter)
     ORDER BY e.embedding <=> query_vec
     LIMIT LEAST(match_count, 1000);
END;
$$;

COMMENT ON FUNCTION public.knn_search_esm2(text, int, text[]) IS
  'Nearest ESM-2 embeddings to query_uid by cosine similarity, most-similar-first. similarity = 1 - cosine_distance. match_count is capped at 1000. species_filter (NULL/empty = all) restricts the search to the named proteins.species values BEFORE the LIMIT, returning the nearest matches within that set.';

-- ─── 2. list_embedding_species (feeds the UI selector) ────────────────────
CREATE OR REPLACE FUNCTION public.list_embedding_species()
RETURNS TABLE (
  species    text,
  n_proteins bigint
)
LANGUAGE sql STABLE
AS $$
  SELECT p.species, count(*)::bigint AS n_proteins
    FROM public.proteins p
    JOIN public.protein_embeddings_esm2 e USING (uid)
   WHERE p.species IS NOT NULL
   GROUP BY p.species
   ORDER BY p.species;
$$;

COMMENT ON FUNCTION public.list_embedding_species() IS
  'Distinct proteins.species that have at least one ESM-2 embedding, with counts. Source of truth for the AI Orthologs species filter — only species that can appear in a KNN result are listed.';

-- ─── 3. Function-level GRANTs (mirror the embedtree base grants) ──────────
GRANT EXECUTE ON FUNCTION public.knn_search_esm2(text, int, text[])
  TO bloom_user, bloom_agent, bloom_admin;
GRANT EXECUTE ON FUNCTION public.list_embedding_species()
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
