-- Migration: best_match_per_accession
--
-- Surface A replacement. Helixer assigns per-genome gene IDs, so a gene exists in
-- exactly one accession and a gene_id join has nothing to rank. Compare by
-- EMBEDDING instead: for a chosen query protein, return the nearest protein(s) in
-- each OTHER accession by ESM-3 cosine.
--
--   best_match_per_accession(query_uid, per_accession, match_count)
--     For each OTHER accession (query's own excluded), its top `per_accession`
--     nearest proteins to query_uid (rank_in_accession = 1..per_accession),
--     ordered most-similar-first overall, total rows capped at match_count.
--     per_accession = 1 gives one best row per accession.
--
-- Implementation: pull the query's ~1000 globally-nearest proteins from the HNSW
-- index (hnsw.ef_search caps at 1000), then keep the top `per_accession` per
-- accession via a window rank. Index-speed — no per-accession full scan. Raising
-- per_accession adds depth without dropping accession coverage (the candidate
-- pool is unchanged). A divergent gene whose ortholog sits beyond the 1000
-- nearest may not surface every accession, so the caller shows the count.
--
-- Depends on protein_embeddings_esm3 / proteins.accession_id / the HNSW index
-- from 20260708000000. STABLE, read-only.

BEGIN;

DROP FUNCTION IF EXISTS public.best_match_per_accession(text, int);
DROP FUNCTION IF EXISTS public.best_match_per_accession(text, int, int);

CREATE OR REPLACE FUNCTION public.best_match_per_accession(
  query_uid     text,
  per_accession int DEFAULT 1,
  match_count   int DEFAULT 1000
)
RETURNS TABLE (
  accession_id      bigint,
  accession_name    text,
  uid               text,
  gene_id           text,
  similarity        float,
  rank_in_accession int
)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
  query_vec vector(1536);
  query_acc bigint;
  per_k     int := GREATEST(1, LEAST(per_accession, 25));  -- clamp per-accession depth
BEGIN
  SELECT e.embedding, p.accession_id
    INTO query_vec, query_acc
    FROM public.protein_embeddings_esm3 e
    JOIN public.proteins p ON p.uid = e.uid
   WHERE e.uid = query_uid;

  IF query_vec IS NULL THEN
    RETURN;
  END IF;

  -- hnsw.ef_search is capped by pgvector at 1000 and must be >= the candidate
  -- LIMIT for HNSW to return that many rows, so pull the max 1000 nearest.
  PERFORM set_config('hnsw.ef_search', '1000', true);

  RETURN QUERY
    WITH near AS (
      SELECT p.accession_id AS acc, p.uid AS puid, p.gene_id AS pgene,
             (e.embedding <=> query_vec) AS dist
        FROM public.protein_embeddings_esm3 e
        JOIN public.proteins p ON p.uid = e.uid
       WHERE p.accession_id IS NOT NULL
         AND p.accession_id <> query_acc
       ORDER BY e.embedding <=> query_vec
       LIMIT 1000
    ),
    ranked AS (
      SELECT near.acc, near.puid, near.pgene, near.dist,
             ROW_NUMBER() OVER (PARTITION BY near.acc ORDER BY near.dist) AS rnk
        FROM near
    )
    SELECT r.acc, a.common_name, r.puid, r.pgene, (1 - r.dist)::float, r.rnk::int
      FROM ranked r
      JOIN public.arabidopsis_accessions a ON a.id = r.acc
     WHERE r.rnk <= per_k
     ORDER BY r.dist ASC
     LIMIT GREATEST(1, LEAST(match_count, 1000));
END;
$$;

COMMENT ON FUNCTION public.best_match_per_accession(text, int, int) IS
  'For query_uid, returns the top per_accession nearest proteins in each OTHER accession by ESM-3 cosine (rank_in_accession 1..per_accession; query''s own accession excluded), ordered most-similar-first, total rows capped at match_count. per_accession=1 is one best row per accession. HNSW-backed 1000-candidate pool (index-speed); per_accession clamped to 25, match_count to 1000.';

GRANT EXECUTE ON FUNCTION public.best_match_per_accession(text, int, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
