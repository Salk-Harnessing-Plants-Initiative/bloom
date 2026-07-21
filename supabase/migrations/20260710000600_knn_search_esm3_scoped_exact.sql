-- Migration: knn_search_esm3_scoped_exact
--
-- knn_search_esm3 ("Find similar proteins") searched the whole HNSW index and
-- post-filtered to accession proteins (accession_id IS NOT NULL). The HNSW index
-- returns only ~ef_search nearest candidates across ALL rows, so when non-
-- accession rows fill that window the accession filter is starved: distant
-- neighbours are dropped and, at match_count=1, the query's own row consumes the
-- only slot before self-exclusion runs -- the RPC returns too few rows (0 at K=1).
-- Widening ef_search only shrinks the window, it does not remove the failure.
--
-- Fix: compute cosine distance EXACTLY over the accession subset in a
-- MATERIALIZED CTE (no ORDER BY / LIMIT inside it, so the planner cannot use the
-- HNSW index or push a LIMIT below the self-exclusion), then order + limit
-- outside. Correct regardless of what else lives in protein_embeddings_esm3.
-- match_count is still clamped to [1, 1000].

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

  -- MATERIALIZED forces the accession filter + self-exclusion to run BEFORE any
  -- ordering: exact distances are computed over accession proteins only, so the
  -- shared (not accession-scoped) HNSW index and its LIMIT push-down can never
  -- starve the result or let the self-row take a real neighbour's slot.
  RETURN QUERY
    WITH scoped AS MATERIALIZED (
      SELECT p.uid,
             p.accession_id,
             a.common_name AS accession_name,
             p.gene_id,
             (e.embedding <=> query_vec) AS dist
        FROM public.protein_embeddings_esm3 e
        JOIN public.proteins p               ON p.uid = e.uid
        JOIN public.arabidopsis_accessions a ON a.id  = p.accession_id
       WHERE p.accession_id IS NOT NULL
         AND e.uid <> query_uid
    )
    SELECT s.uid,
           s.accession_id,
           s.accession_name,
           s.gene_id,
           (1 - s.dist)::float AS similarity
      FROM scoped s
     ORDER BY s.dist
     LIMIT capped;
END;
$$;

COMMENT ON FUNCTION public.knn_search_esm3(text, int) IS
  'Surface B. Returns the match_count nearest accession proteins to query_uid by ESM-3 cosine similarity, most-similar-first, EXCLUDING query_uid itself so match_count = number of neighbors. Exact scan over accession proteins (accession_id IS NOT NULL) via a MATERIALIZED CTE, so it does not use the HNSW index and stays correct regardless of any non-accession rows in the table. similarity = 1 - cosine_distance. match_count clamped to [1, 1000].';

GRANT EXECUTE ON FUNCTION public.knn_search_esm3(text, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
