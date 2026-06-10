-- =============================================================================
-- 20260610000100_create_orthogroups.sql
--
-- This table would let us compare the orthofinder results with the embedding based orthologs.
--
-- Adds the OrthoFinder cross-reference layer for the embedtree feature.
-- The orthogroups table maps the existing (gene_id, species) → orthogroup. 
-- The get_orthogroup_info(query_gene_id, result_gene_ids) function returns
-- which of a candidate gene set shares an orthogroup with the query —
-- used by the UI to highlight KNN results that also share orthology.
--
-- Orthogroups are independent of embedding model: the same orthogroups
-- table is consulted regardless of which knn_search_<suffix> produced
-- the candidate gene list.
--
-- RLS pattern: same admin_all / agent_read / user_read as the embedtree
-- schema (20260610000000_create_embedtree_schema.sql), mirroring
-- 20260603000000_fix_gene_progress_logs.sql.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, DROP POLICY IF EXISTS,
-- DROP FUNCTION IF EXISTS.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS public.orthogroups (
  gene_id     text        NOT NULL,
  species     text        NOT NULL,
  orthogroup  text        NOT NULL,
  raw_gene_id text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (gene_id, species, orthogroup)
);

CREATE INDEX IF NOT EXISTS orthogroups_gene_id_idx    ON public.orthogroups (gene_id);
CREATE INDEX IF NOT EXISTS orthogroups_orthogroup_idx ON public.orthogroups (orthogroup);
CREATE INDEX IF NOT EXISTS orthogroups_species_idx    ON public.orthogroups (species);

COMMENT ON TABLE public.orthogroups IS
  'OrthoFinder orthogroup mappings: (gene_id, species) -> orthogroup. The (gene_id, species, orthogroup) primary key + ON CONFLICT lets the ingest script be idempotent. Embedding-model-independent.';

-- ─── get_orthogroup_info(query_gene_id, result_gene_ids) ──────────────────
DROP FUNCTION IF EXISTS public.get_orthogroup_info(text, text[]);

CREATE OR REPLACE FUNCTION public.get_orthogroup_info(
  query_gene_id   text,
  result_gene_ids text[]
)
RETURNS TABLE (
  gene_id            text,
  orthogroup         text,
  shared_with_query  boolean
)
LANGUAGE sql STABLE
AS $$
  WITH query_ogs AS (
    SELECT DISTINCT orthogroup
      FROM public.orthogroups
     WHERE lower(gene_id) = lower(query_gene_id)
  )
  SELECT o.gene_id,
         o.orthogroup,
         EXISTS (
           SELECT 1 FROM query_ogs q WHERE q.orthogroup = o.orthogroup
         ) AS shared_with_query
    FROM public.orthogroups o
   WHERE lower(o.gene_id) = ANY (
           SELECT lower(g) FROM unnest(result_gene_ids) AS g
         );
$$;

COMMENT ON FUNCTION public.get_orthogroup_info(text, text[]) IS
  'For each gene_id in result_gene_ids, returns (gene_id, orthogroup, shared_with_query) where shared_with_query = true iff that gene shares an orthogroup with query_gene_id. Case-insensitive on gene_id to absorb upstream casing inconsistencies.';

-- ─── RLS + grants ─────────────────────────────────────────────────────────
ALTER TABLE public.orthogroups ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS admin_all_orthogroups  ON public.orthogroups;
CREATE POLICY admin_all_orthogroups
  ON public.orthogroups FOR ALL    TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_orthogroups ON public.orthogroups;
CREATE POLICY agent_read_orthogroups
  ON public.orthogroups FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_orthogroups  ON public.orthogroups;
CREATE POLICY user_read_orthogroups
  ON public.orthogroups FOR SELECT TO bloom_user  USING (true);

GRANT SELECT ON public.orthogroups TO bloom_user, bloom_agent;
GRANT ALL    ON public.orthogroups TO bloom_admin;

GRANT EXECUTE ON FUNCTION public.get_orthogroup_info(text, text[])
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
