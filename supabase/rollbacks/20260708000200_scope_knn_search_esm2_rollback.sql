-- Manual rollback for 20260708000200_scope_knn_search_esm2.sql
--
-- Restores knn_search_esm2 to its original body (from 20260610000000): no
-- accession_id filter, plain LEAST(match_count, 1000) LIMIT.

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

  RETURN QUERY
    SELECT p.uid,
           p.species,
           p.gene_id,
           (1 - (e.embedding <=> query_vec))::float AS similarity
      FROM public.protein_embeddings_esm2 e
      JOIN public.proteins p USING (uid)
     ORDER BY e.embedding <=> query_vec
     LIMIT LEAST(match_count, 1000);
END;
$$;

GRANT EXECUTE ON FUNCTION public.knn_search_esm2(text, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
