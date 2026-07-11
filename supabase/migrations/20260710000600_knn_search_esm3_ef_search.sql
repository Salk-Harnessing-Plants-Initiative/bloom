-- Migration: knn_search_esm3_ef_search
--
-- knn_search_esm3 ("Find similar proteins") returns the match_count nearest
-- accession proteins from the HNSW index, but never set hnsw.ef_search — so with
-- the default (40) a request for more than ~40 neighbours silently returned
-- fewer than asked. Raising the UI's K ceiling to 1000 (pgvector's ef_search
-- max) is meaningless unless the function widens the index search to match.
--
-- Fix: set hnsw.ef_search to the requested count (transaction-local, clamped to
-- [40, 1000] — pgvector's own max) before the KNN, plus a small margin so the
-- self-exclusion doesn't drop the last neighbour. Behaviour otherwise unchanged;
-- match_count is still hard-capped at 1000 (the index cannot return more).

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
  -- HNSW index push the LIMIT below the filter and silently drop a real
  -- neighbour to the query protein's own slot.
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
     ORDER BY t.dist
     LIMIT capped;
END;
$$;

COMMENT ON FUNCTION public.knn_search_esm3(text, int) IS
  'Surface B. Returns the match_count nearest accession proteins to query_uid by ESM-3 cosine similarity, most-similar-first, EXCLUDING query_uid itself. Sets hnsw.ef_search to the requested count so large K actually returns that many. Restricted to accession_id IS NOT NULL. similarity = 1 - cosine_distance. match_count hard-capped at 1000 (the HNSW index cannot return more).';

GRANT EXECUTE ON FUNCTION public.knn_search_esm3(text, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
