-- =============================================================================
-- 20260708000100_accession_comparison_rpcs.sql
--
-- Query surface for the single-species Arabidopsis-accession embedding layer
-- added in 20260708000000. Two comparison surfaces + one autocomplete, plus a
-- scoping fix to the cross-species search_genes so the two surfaces stay
-- disjoint now that they share the proteins table.
--
--   knn_search_esm3(query_uid, match_count)
--       Surface B — pan-proteome KNN across accession proteins by ESM-3 cosine.
--       Filtered to accession_id IS NOT NULL so an ESM-3 embedding accidentally
--       loaded for a cross-species protein can never bleed in (isolation in the
--       RPC, not just the picker).
--
--   compare_gene_across_accessions(target_gene_id, reference_uid, match_count)
--       Surface A — for a fixed gene_id, each accession's variant ranked by
--       ESM-3 cosine similarity to a reference. Reference precedence:
--       explicit reference_uid → the is_reference accession's variant →
--       lexicographically-first variant. A reference_uid from a different gene
--       returns zero rows. The reference row (similarity 1.0) always sorts
--       first, so it survives truncation.
--
--   search_accession_genes(partial_id, max_results)
--       Autocomplete over accession proteins only (accession_id IS NOT NULL).
--
--   search_genes(partial_id, max_results)  [scoped]
--       Adds accession_id IS NULL so the cross-species AI Orthologs picker
--       never surfaces accession proteins. Signature unchanged.
--
-- This migration references proteins.accession_id, added by 20260708000000, so
-- it must never be applied without that migration (timestamp order guarantees).
--
-- Idempotent: DROP FUNCTION IF EXISTS before each CREATE where the signature
-- is new; CREATE OR REPLACE for search_genes (unchanged signature).
-- =============================================================================

BEGIN;

-- ─── knn_search_esm3 (Surface B) ──────────────────────────────────────────
DROP FUNCTION IF EXISTS public.knn_search_esm3(text, int);

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
BEGIN
  SELECT embedding INTO query_vec
    FROM public.protein_embeddings_esm3
   WHERE protein_embeddings_esm3.uid = query_uid;

  IF query_vec IS NULL THEN
    RETURN;
  END IF;

  -- accession_id IS NOT NULL keeps Surface B scoped to accession proteins even
  -- if ESM-3 rows are ever loaded for a cross-species protein. Hard cap 1000.
  RETURN QUERY
    SELECT p.uid,
           p.accession_id,
           a.common_name AS accession_name,
           p.gene_id,
           (1 - (e.embedding <=> query_vec))::float AS similarity
      FROM public.protein_embeddings_esm3 e
      JOIN public.proteins p               ON p.uid = e.uid
      JOIN public.arabidopsis_accessions a ON a.id  = p.accession_id
     WHERE p.accession_id IS NOT NULL
     ORDER BY e.embedding <=> query_vec
     LIMIT LEAST(match_count, 1000);
END;
$$;

COMMENT ON FUNCTION public.knn_search_esm3(text, int) IS
  'Surface B. Returns the match_count nearest accession proteins to query_uid by ESM-3 cosine similarity, most-similar-first. Restricted to accession_id IS NOT NULL. similarity = 1 - cosine_distance. match_count hard-capped at 1000.';

-- ─── compare_gene_across_accessions (Surface A) ───────────────────────────
DROP FUNCTION IF EXISTS public.compare_gene_across_accessions(text, text, int);

CREATE OR REPLACE FUNCTION public.compare_gene_across_accessions(
  target_gene_id text,
  reference_uid  text DEFAULT NULL,
  match_count    int  DEFAULT 1000
)
RETURNS TABLE (
  uid            text,
  accession_id   bigint,
  accession_name text,
  gene_id        text,
  similarity     float,
  is_reference   boolean
)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
  ref_uid text;
  ref_vec vector(1536);
BEGIN
  IF char_length(btrim(coalesce(target_gene_id, ''))) < 1 THEN
    RETURN;
  END IF;

  IF reference_uid IS NOT NULL THEN
    -- Explicit reference must belong to target_gene_id, be an accession
    -- protein, and have an ESM-3 embedding. Otherwise return nothing rather
    -- than rank one gene's variants against another gene's embedding.
    SELECT p.uid INTO ref_uid
      FROM public.proteins p
      JOIN public.protein_embeddings_esm3 e ON e.uid = p.uid
     WHERE p.uid          = reference_uid
       AND p.gene_id      = target_gene_id
       AND p.accession_id IS NOT NULL;
    IF ref_uid IS NULL THEN
      RETURN;
    END IF;
  ELSE
    -- Default reference: the is_reference accession's variant of this gene,
    -- else the lexicographically-first variant. Deterministic.
    SELECT p.uid INTO ref_uid
      FROM public.proteins p
      JOIN public.protein_embeddings_esm3 e ON e.uid = p.uid
      JOIN public.arabidopsis_accessions a  ON a.id  = p.accession_id
     WHERE p.gene_id      = target_gene_id
       AND p.accession_id IS NOT NULL
     ORDER BY a.is_reference DESC, p.uid ASC
     LIMIT 1;
    IF ref_uid IS NULL THEN
      RETURN;
    END IF;
  END IF;

  SELECT embedding INTO ref_vec
    FROM public.protein_embeddings_esm3
   WHERE protein_embeddings_esm3.uid = ref_uid;

  -- Reference row sorts first (its similarity is 1.0), so it always survives
  -- the LEAST(match_count, 1000) truncation.
  RETURN QUERY
    SELECT p.uid,
           p.accession_id,
           a.common_name AS accession_name,
           p.gene_id,
           (1 - (e.embedding <=> ref_vec))::float AS similarity,
           (p.uid = ref_uid) AS is_reference
      FROM public.protein_embeddings_esm3 e
      JOIN public.proteins p               ON p.uid = e.uid
      JOIN public.arabidopsis_accessions a ON a.id  = p.accession_id
     WHERE p.gene_id      = target_gene_id
       AND p.accession_id IS NOT NULL
     ORDER BY (p.uid = ref_uid) DESC, similarity DESC, p.uid ASC
     LIMIT LEAST(match_count, 1000);
END;
$$;

COMMENT ON FUNCTION public.compare_gene_across_accessions(text, text, int) IS
  'Surface A. For a fixed target_gene_id, returns each accession variant that has an ESM-3 embedding, ranked by cosine similarity to a reference variant. Reference precedence: reference_uid → is_reference accession → lexicographically-first. reference_uid from a different gene returns zero rows. Reference row is is_reference=true, similarity=1.0, always included. match_count hard-capped at 1000.';

-- ─── search_accession_genes (autocomplete, accession-scoped) ──────────────
DROP FUNCTION IF EXISTS public.search_accession_genes(text, int);

CREATE OR REPLACE FUNCTION public.search_accession_genes(
  partial_id  text,
  max_results int DEFAULT 20
)
RETURNS TABLE (
  uid            text,
  accession_id   bigint,
  accession_name text,
  gene_id        text
)
LANGUAGE sql STABLE
AS $$
  -- Empty / whitespace-only / NULL partial_id returns zero rows (same gate as
  -- search_genes). Scoped to accession proteins only. Hard cap at 1000.
  SELECT p.uid, p.accession_id, a.common_name AS accession_name, p.gene_id
    FROM public.proteins p
    JOIN public.arabidopsis_accessions a ON a.id = p.accession_id
   WHERE p.accession_id IS NOT NULL
     AND char_length(btrim(partial_id)) >= 1
     AND ( p.uid     ILIKE '%' || partial_id || '%'
        OR p.gene_id ILIKE '%' || partial_id || '%' )
   ORDER BY p.uid
   LIMIT LEAST(max_results, 1000);
$$;

COMMENT ON FUNCTION public.search_accession_genes(text, int) IS
  'Case-insensitive substring match on accession proteins (accession_id IS NOT NULL) by uid or gene_id, joined to the accession name. Mirror of search_genes for the accession gene picker. Empty/whitespace/NULL returns zero rows; max_results hard-capped at 1000.';

-- ─── search_genes (scoped to cross-species proteins) ──────────────────────
-- Signature unchanged, so CREATE OR REPLACE (no DROP). Adds accession_id IS
-- NULL so accession proteins never appear in the cross-species picker.
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
  -- accession_id IS NULL restricts to cross-species proteins. Empty /
  -- whitespace-only / NULL partial_id returns zero rows. Hard cap at 1000.
  SELECT uid, species, gene_id
    FROM public.proteins
   WHERE accession_id IS NULL
     AND char_length(btrim(partial_id)) >= 1
     AND ( uid     ILIKE '%' || partial_id || '%'
        OR gene_id ILIKE '%' || partial_id || '%' )
   ORDER BY uid
   LIMIT LEAST(max_results, 1000);
$$;

COMMENT ON FUNCTION public.search_genes(text, int) IS
  'Case-insensitive substring match on proteins.uid or proteins.gene_id, restricted to cross-species proteins (accession_id IS NULL) so accession proteins never surface in the cross-species AI Orthologs picker. Empty/whitespace/NULL returns zero rows; max_results hard-capped at 1000.';

-- ─── Function-level GRANTs ────────────────────────────────────────────────
GRANT EXECUTE ON FUNCTION public.knn_search_esm3(text, int)
  TO bloom_user, bloom_agent, bloom_admin;
GRANT EXECUTE ON FUNCTION public.compare_gene_across_accessions(text, text, int)
  TO bloom_user, bloom_agent, bloom_admin;
GRANT EXECUTE ON FUNCTION public.search_accession_genes(text, int)
  TO bloom_user, bloom_agent, bloom_admin;
GRANT EXECUTE ON FUNCTION public.search_genes(text, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
