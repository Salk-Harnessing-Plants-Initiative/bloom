-- Migration: knn_search_esm3_revert_hnsw
--
-- 20260710000600 replaced knn_search_esm3 ("Find similar proteins") with an exact
-- MATERIALIZED scan to guarantee complete recall. At production scale (~12.4M
-- embeddings) that scan is a full sequential scan on every call and hits the
-- database statement timeout ("canceling statement due to statement timeout"),
-- so the tab returns nothing.
--
-- Revert to the fast, HNSW-index-backed query: set hnsw.ef_search to the
-- requested count (+ a small margin so self-exclusion doesn't drop the last
-- neighbour), fetch capped+1 nearest, exclude the query protein, return capped
-- most-similar-first. Approximate recall is acceptable for this exploratory
-- surface (the page carries a "predicted, not verified" banner). match_count is
-- clamped to [1, 1000] (the HNSW index cannot return more).

BEGIN;

CREATE OR REPLACE FUNCTION public.knn_search_esm3(
  query_uid   text,
  match_count int DEFAULT 20
)
RETURNS TABLE (
  uid            text,
  accession_id   bigint,
  accession_name text,
  gene_id        text,
  similarity     float
)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
  query_vec vector(1536);
  capped    int := GREATEST(1, LEAST(match_count, 1000));
BEGIN
  SELECT embedding INTO query_vec
    FROM public.protein_embeddings_esm3
   WHERE protein_embeddings_esm3.uid = query_uid;

  IF query_vec IS NULL THEN
    RETURN;
  END IF;

  -- Widen the index search to the requested count + margin, clamped to
  -- pgvector's [1, 1000] ef_search range.
  PERFORM set_config('hnsw.ef_search', LEAST(capped + 10, 1000)::text, true);

  -- Fetch capped+1 nearest (self is always nearest, distance 0), THEN exclude
  -- self and take capped. Excluding self inside the ORDER BY..LIMIT would let the
  -- HNSW index push the LIMIT below the filter and silently drop a real neighbour
  -- to the query protein's own slot.
  RETURN QUERY
    SELECT t.uid, t.accession_id, t.accession_name, t.gene_id, t.similarity
      FROM (
        SELECT p.uid,
               p.accession_id,
               a.common_name AS accession_name,
               p.gene_id,
               (1 - (e.embedding <=> query_vec))::float AS similarity,
               (e.embedding <=> query_vec) AS dist
          FROM public.protein_embeddings_esm3 e
          JOIN public.proteins p               ON p.uid = e.uid
          JOIN public.arabidopsis_accessions a ON a.id  = p.accession_id
         WHERE p.accession_id IS NOT NULL
         ORDER BY e.embedding <=> query_vec
         LIMIT capped + 1
      ) t
     WHERE t.uid <> query_uid
     -- Tiebreak on uid so exact-distance ties order deterministically (stable
     -- CSV export). NB: does not make the approximate HNSW candidate SET
     -- reproducible — only the ordering of whatever set is returned.
     ORDER BY t.dist, t.uid
     LIMIT capped;
END;
$$;

COMMENT ON FUNCTION public.knn_search_esm3(text, int) IS
  'Surface B. Returns the match_count nearest accession proteins to query_uid by ESM-3 cosine similarity, most-similar-first, EXCLUDING query_uid itself. HNSW-index-backed (sets hnsw.ef_search to the requested count) so it stays fast at production scale. Restricted to accession_id IS NOT NULL. similarity = 1 - cosine_distance. match_count hard-capped at 1000.';

GRANT EXECUTE ON FUNCTION public.knn_search_esm3(text, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
