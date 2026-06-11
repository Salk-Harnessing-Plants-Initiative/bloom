-- =============================================================================
-- 20260610000000_create_embedtree_schema.sql
--
-- Adds the orthofinder 2 (protein-embedding phylogenomics) schema using a
-- multi-model registry pattern, sized for ESM-2 today and designed for
-- easy extension to ESM-3 / ProtBERT / etc. tomorrow.
--
-- Layout:
--   protein_embedding_models   registry for protein-embedding models
--   proteins                   model-independent gene metadata
--   protein_embeddings_esm2    per-model vector(1280) embeddings (ESM-2)
--   rbh_cache_esm2             per-model reciprocal best hits cache (ESM-2)
--   knn_search_esm2(...)       per-model KNN RPC
--   search_genes(...)          model-independent metadata search RPC
--
-- The pattern (registry + per-model tables + cosine-indexed vector(N)
-- column) is domain-agnostic. Future non-protein embedding domains
-- (e.g. nucleotide sequences, research-paper text) would add sibling
-- tables following the same shape (nucleotide_embedding_models +
-- nucleotides + nucleotide_embeddings_<model>; paper_embedding_models
-- + papers + paper_embeddings_<model>) — each domain isolated, and the
-- type system rejects vectors of the wrong dimension at insert time.
-- That's why the registry is named protein_embedding_models rather
-- than a generic 'embedding_models', leaving the latter free for a
-- cross-domain registry if ever needed.
--
-- Future PROTEIN models add their own migration that:
--   1. INSERTs a row into protein_embedding_models with a new
--      table_suffix
--   2. CREATEs protein_embeddings_<suffix> / rbh_cache_<suffix>
--   3. CREATEs knn_search_<suffix>(query_uid, match_count)
--   4. Applies the same 3-policy bloom_* RLS pattern + grants
--
-- RLS pattern 
--   admin_all_<table>  FOR ALL    TO bloom_admin USING true WITH CHECK true
--   agent_read_<table> FOR SELECT TO bloom_agent USING true
--   user_read_<table>  FOR SELECT TO bloom_user  USING true
-- Writes happen only via supabase_admin (service-role key)
-- No bloom_writer policies because ingest does not use a
-- bloom_writer JWT — one-off operator scripts use the service role.
--
-- The migration is idempotent: every CREATE POLICY / CREATE FUNCTION is
-- preceded by DROP IF EXISTS, every CREATE TABLE uses IF NOT EXISTS,
-- the seed INSERT uses ON CONFLICT DO NOTHING.
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

-- ─── protein_embedding_models registry ────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.protein_embedding_models (
  model_id     text        PRIMARY KEY,
  display_name text        NOT NULL,
  dimension    int         NOT NULL,
  table_suffix text        NOT NULL UNIQUE,
  description  text,
  is_active    boolean     NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.protein_embedding_models IS
  'Registry of protein-embedding models. One row per registered model. table_suffix drives the names of protein_embeddings_<suffix>, rbh_cache_<suffix>, and knn_search_<suffix>. Domain-scoped to proteins by design; future non-protein domains get their own sibling registry (e.g. nucleotide_embedding_models, paper_embedding_models).';

INSERT INTO public.protein_embedding_models
  (model_id,              display_name,           dimension, table_suffix, description)
VALUES
  ('esm2_t33_650M_UR50D', 'ESM-2 (650M, UR50D)',  1280,      'esm2',
   'ESM-2 protein language model from FAIR (2022), 650M params, trained on UR50D')
ON CONFLICT (model_id) DO NOTHING;

-- ─── proteins (model-independent metadata) ────────────────────────────────
CREATE TABLE IF NOT EXISTS public.proteins (
  uid         text        PRIMARY KEY,
  species     text,
  gene_id     text,
  raw_gene_id text,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS proteins_species_idx     ON public.proteins (species);
CREATE INDEX IF NOT EXISTS proteins_gene_id_idx     ON public.proteins (gene_id);
CREATE INDEX IF NOT EXISTS proteins_raw_gene_id_idx ON public.proteins (raw_gene_id);

COMMENT ON TABLE public.proteins IS
  'Per-protein gene metadata, model-independent. Embeddings live in per-model protein_embeddings_<suffix> tables linked via uid FK.';

-- ─── protein_embeddings_esm2 (per-model) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS public.protein_embeddings_esm2 (
  uid        text         PRIMARY KEY REFERENCES public.proteins(uid) ON DELETE CASCADE,
  embedding  vector(1280) NOT NULL,
  created_at timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS protein_embeddings_esm2_ivfflat_idx
  ON public.protein_embeddings_esm2
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

COMMENT ON TABLE public.protein_embeddings_esm2 IS
  'ESM-2 protein embeddings, vector(1280) cosine-indexed. Each row corresponds to a proteins.uid. Postgres rejects vectors of any other dimension at the type-cast boundary — this is the cross-model contamination guardrail.';

-- ─── rbh_cache_esm2 (per-model) ───────────────────────────────────────────
-- RBH is symmetric: The CHECK below forces a canonical (species_1 < species_2)
-- ordering at the schema level so the same biological pair can't be stored twice under swapped keys. 
-- Ingest must `SELECT LEAST(s1,s2), GREATEST(s1,s2)` before writing.
CREATE TABLE IF NOT EXISTS public.rbh_cache_esm2 (
  species_1     text        NOT NULL,
  species_2     text        NOT NULL,
  metric        text        NOT NULL,
  rbh_count     int         NOT NULL,
  mean_distance float       NOT NULL,
  computed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (species_1, species_2, metric),
  CONSTRAINT rbh_cache_esm2_species_canonical CHECK (species_1 < species_2)
);

COMMENT ON TABLE public.rbh_cache_esm2 IS
  'Precomputed reciprocal best hits within ESM-2 embedding space, per species pair × metric. species_1 < species_2 is enforced by CHECK so each symmetric pair is stored exactly once. RBH is meaningful only within one model''s embedding space, so this table is per-model.';

-- ─── knn_search_esm2 (per-model RPC) ──────────────────────────────────────
DROP FUNCTION IF EXISTS public.knn_search_esm2(text, int);

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
     LIMIT match_count;
END;
$$;

COMMENT ON FUNCTION public.knn_search_esm2(text, int) IS
  'Returns the match_count nearest ESM-2 embeddings to query_uid by cosine similarity, ordered most-similar-first. similarity = 1 - cosine_distance.';

-- ─── search_genes (model-independent RPC) ─────────────────────────────────
DROP FUNCTION IF EXISTS public.search_genes(text, int);

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
  SELECT uid, species, gene_id
    FROM public.proteins
   WHERE uid     ILIKE '%' || partial_id || '%'
      OR gene_id ILIKE '%' || partial_id || '%'
   ORDER BY uid
   LIMIT max_results;
$$;

COMMENT ON FUNCTION public.search_genes(text, int) IS
  'Case-insensitive substring match on proteins.uid or proteins.gene_id. Used by the embedtree UI gene picker autocomplete. Model-independent.';

-- ─── RLS enable ───────────────────────────────────────────────────────────
ALTER TABLE public.protein_embedding_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.proteins                ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.protein_embeddings_esm2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rbh_cache_esm2          ENABLE ROW LEVEL SECURITY;

-- ─── RLS policies: admin_all / agent_read / user_read on each table ───────

-- protein_embedding_models
DROP POLICY IF EXISTS admin_all_protein_embedding_models  ON public.protein_embedding_models;
CREATE POLICY admin_all_protein_embedding_models
  ON public.protein_embedding_models FOR ALL    TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_protein_embedding_models ON public.protein_embedding_models;
CREATE POLICY agent_read_protein_embedding_models
  ON public.protein_embedding_models FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_protein_embedding_models  ON public.protein_embedding_models;
CREATE POLICY user_read_protein_embedding_models
  ON public.protein_embedding_models FOR SELECT TO bloom_user  USING (true);

-- proteins
DROP POLICY IF EXISTS admin_all_proteins  ON public.proteins;
CREATE POLICY admin_all_proteins
  ON public.proteins FOR ALL    TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_proteins ON public.proteins;
CREATE POLICY agent_read_proteins
  ON public.proteins FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_proteins  ON public.proteins;
CREATE POLICY user_read_proteins
  ON public.proteins FOR SELECT TO bloom_user  USING (true);

-- protein_embeddings_esm2
DROP POLICY IF EXISTS admin_all_protein_embeddings_esm2  ON public.protein_embeddings_esm2;
CREATE POLICY admin_all_protein_embeddings_esm2
  ON public.protein_embeddings_esm2 FOR ALL    TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_protein_embeddings_esm2 ON public.protein_embeddings_esm2;
CREATE POLICY agent_read_protein_embeddings_esm2
  ON public.protein_embeddings_esm2 FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_protein_embeddings_esm2  ON public.protein_embeddings_esm2;
CREATE POLICY user_read_protein_embeddings_esm2
  ON public.protein_embeddings_esm2 FOR SELECT TO bloom_user  USING (true);

-- rbh_cache_esm2
DROP POLICY IF EXISTS admin_all_rbh_cache_esm2  ON public.rbh_cache_esm2;
CREATE POLICY admin_all_rbh_cache_esm2
  ON public.rbh_cache_esm2 FOR ALL    TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_rbh_cache_esm2 ON public.rbh_cache_esm2;
CREATE POLICY agent_read_rbh_cache_esm2
  ON public.rbh_cache_esm2 FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_rbh_cache_esm2  ON public.rbh_cache_esm2;
CREATE POLICY user_read_rbh_cache_esm2
  ON public.rbh_cache_esm2 FOR SELECT TO bloom_user  USING (true);

-- ─── Table-level GRANTs (PostgREST requires both policy AND grant) ────────
GRANT SELECT ON public.protein_embedding_models TO bloom_user, bloom_agent;
GRANT ALL    ON public.protein_embedding_models TO bloom_admin;

GRANT SELECT ON public.proteins                TO bloom_user, bloom_agent;
GRANT ALL    ON public.proteins                TO bloom_admin;

GRANT SELECT ON public.protein_embeddings_esm2 TO bloom_user, bloom_agent;
GRANT ALL    ON public.protein_embeddings_esm2 TO bloom_admin;

GRANT SELECT ON public.rbh_cache_esm2          TO bloom_user, bloom_agent;
GRANT ALL    ON public.rbh_cache_esm2          TO bloom_admin;

-- ─── Function-level GRANTs ────────────────────────────────────────────────
GRANT EXECUTE ON FUNCTION public.knn_search_esm2(text, int)
  TO bloom_user, bloom_agent, bloom_admin;
GRANT EXECUTE ON FUNCTION public.search_genes(text, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
