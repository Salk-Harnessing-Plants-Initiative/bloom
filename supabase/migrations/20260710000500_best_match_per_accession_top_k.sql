-- Migration: best_match_per_accession_top_k
--
-- Supersedes the top-1 best_match_per_accession from 20260710000400 (already
-- applied on staging, so it must not be edited in place). Adds a per_accession
-- argument: for each accession, return its top-N nearest proteins to the query
-- (rank_in_accession 1..N) via a window rank.
--
--   best_match_per_accession(query_uid, per_accession, match_count)
--
-- APPROXIMATE (fast). The candidate set is the query's ~1000 globally-nearest
-- proteins from the HNSW index (hnsw.ef_search caps at 1000), then partitioned by
-- accession. This is index-speed (milliseconds), but coverage is emergent: only
-- accessions represented among those 1000 nearest proteins appear, so a divergent
-- query can surface fewer than the full panel. The UI states this ("among your
-- query's nearest matches") and shows the returned accession count. An EXACT scan
-- over all ~12.4M embeddings is complete but ~3 minutes single-threaded — not
-- viable on demand; fast+complete needs a per-accession (partitioned) vector
-- index, tracked as a follow-up.
--
-- Signature changes (adds a third argument), so the 2-arg function is dropped
-- and replaced; 2-arg callers resolve to the new function via the defaults.

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
  'For query_uid, returns the top per_accession nearest proteins per accession by ESM-3 cosine (rank_in_accession 1..per_accession; query''s own accession excluded), most-similar first. APPROXIMATE: candidate pool is the query''s 1000 nearest proteins from the HNSW index, so coverage is limited to accessions among those nearest matches (index-speed). per_accession clamped to 25, match_count to 1000.';

GRANT EXECUTE ON FUNCTION public.best_match_per_accession(text, int, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
