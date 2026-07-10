-- Migration: best_match_per_accession
--
-- Surface A replacement. "Compare a gene across accessions" via shared gene_id
-- cannot work: Helixer assigns per-genome gene IDs, so a gene exists in exactly
-- one accession and there is nothing to rank. Instead, compare by EMBEDDING: for
-- a chosen query protein, return the single most-similar protein in each OTHER
-- accession (its ESM-3 nearest neighbour within that accession), ranked by
-- cosine similarity, limited to the top match_count accessions.
--
--   best_match_per_accession(query_uid, match_count)
--     One row per OTHER accession (the query's own accession is excluded): that
--     accession's closest protein to query_uid by ESM-3 cosine. Ordered
--     most-similar-first, capped at match_count (the top-K nearest accessions).
--
-- Implementation: pull the query's globally-nearest proteins from the HNSW index
-- (bounded candidate pool sized to cover the top match_count accessions), then
-- keep the best per accession. Index-speed — no per-accession full scan. This is
-- exact for the closest accessions (their best match is among the query's
-- nearest neighbours) and approximate only for the far tail, which top-K never
-- surfaces anyway.
--
-- Depends on protein_embeddings_esm3 / proteins.accession_id / the HNSW index
-- from 20260708000000. STABLE, read-only.

BEGIN;

DROP FUNCTION IF EXISTS public.best_match_per_accession(text, int);

CREATE OR REPLACE FUNCTION public.best_match_per_accession(
  query_uid   text,
  match_count int DEFAULT 20
)
RETURNS TABLE (
  accession_id   bigint,
  accession_name text,
  uid            text,
  gene_id        text,
  similarity     float
)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
  query_vec vector(1536);
  query_acc bigint;
  pool      int;
BEGIN
  SELECT e.embedding, p.accession_id
    INTO query_vec, query_acc
    FROM public.protein_embeddings_esm3 e
    JOIN public.proteins p ON p.uid = e.uid
   WHERE e.uid = query_uid;

  IF query_vec IS NULL THEN
    RETURN;
  END IF;

  -- Candidate pool: enough of the query's globally-nearest proteins that the top
  -- match_count accessions each have their best match represented. Bounded so a
  -- large match_count cannot search unbounded. ef_search must reach the LIMIT
  -- for HNSW to return that many rows.
  pool := LEAST(GREATEST(match_count * 50, 3000), 20000);
  PERFORM set_config('hnsw.ef_search', pool::text, true);

  RETURN QUERY
    WITH near AS (
      SELECT p.accession_id AS acc, p.uid AS puid, p.gene_id AS pgene,
             (e.embedding <=> query_vec) AS dist
        FROM public.protein_embeddings_esm3 e
        JOIN public.proteins p ON p.uid = e.uid
       WHERE p.accession_id IS NOT NULL
         AND p.accession_id <> query_acc
       ORDER BY e.embedding <=> query_vec
       LIMIT pool
    ),
    best AS (
      SELECT DISTINCT ON (near.acc)
             near.acc, near.puid, near.pgene, near.dist
        FROM near
       ORDER BY near.acc, near.dist
    )
    SELECT b.acc, a.common_name, b.puid, b.pgene, (1 - b.dist)::float
      FROM best b
      JOIN public.arabidopsis_accessions a ON a.id = b.acc
     ORDER BY b.dist ASC
     LIMIT GREATEST(1, LEAST(match_count, 1000));
END;
$$;

COMMENT ON FUNCTION public.best_match_per_accession(text, int) IS
  'For query_uid, returns the single most-similar protein in each OTHER accession by ESM-3 cosine (one row per accession, query''s own accession excluded), ranked most-similar-first, capped at match_count (top-K nearest accessions). HNSW-backed candidate pool, so index-speed and exact for the closest accessions. Replaces gene_id-join comparison, which cannot work with per-genome Helixer gene IDs.';

GRANT EXECUTE ON FUNCTION public.best_match_per_accession(text, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
