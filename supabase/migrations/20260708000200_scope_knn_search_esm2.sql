-- =============================================================================
-- 20260708000200_scope_knn_search_esm2.sql
--
-- Makes the cross-species / accession isolation symmetric. The accession
-- surface's knn_search_esm3 (20260708000100) filters accession_id IS NOT NULL;
-- this adds the mirror guard to the cross-species knn_search_esm2 so that if an
-- accession protein ever receives an ESM-2 embedding it can never bleed into
-- the cross-species graph. Also hardens the LIMIT to GREATEST(1, LEAST(...))
-- so a negative match_count (reachable via direct PostgREST) can't error.
--
-- Unlike knn_search_esm3, this KEEPS the query's own row (self-match, similarity
-- 1.0) — the /app/embedtree UI relies on the query appearing at the top of its
-- graph, and the integration test asserts it. Only the accession scope and the
-- LIMIT guard change; ordering and shape are unchanged.
--
-- CREATE OR REPLACE (signature unchanged). Idempotent.
-- =============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.knn_search_esm2(
  query_uid   text,
  match_count int DEFAULT 20
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

  -- accession_id IS NULL keeps the cross-species graph free of accession
  -- proteins (mirror of knn_search_esm3's accession_id IS NOT NULL guard).
  -- LIMIT guarded so a negative match_count clamps to 1 instead of erroring.
  RETURN QUERY
    SELECT p.uid,
           p.species,
           p.gene_id,
           (1 - (e.embedding <=> query_vec))::float AS similarity
      FROM public.protein_embeddings_esm2 e
      JOIN public.proteins p ON p.uid = e.uid
     WHERE p.accession_id IS NULL
     ORDER BY e.embedding <=> query_vec
     LIMIT GREATEST(1, LEAST(match_count, 1000));
END;
$$;

COMMENT ON FUNCTION public.knn_search_esm2(text, int) IS
  'Returns the match_count nearest ESM-2 embeddings to query_uid by cosine similarity, most-similar-first, restricted to cross-species proteins (accession_id IS NULL). Includes the query itself. similarity = 1 - cosine_distance. match_count clamped to [1, 1000].';

GRANT EXECUTE ON FUNCTION public.knn_search_esm2(text, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
