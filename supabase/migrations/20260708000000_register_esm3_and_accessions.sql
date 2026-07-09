-- =============================================================================
-- 20260708000000_register_esm3_and_accessions.sql
--
-- Adds the single-species Arabidopsis-accession embedding layer on top of the
-- existing multi-model protein-embedding registry (20260610000000). This is
-- the ESM-3 model the original schema's registry was designed to accept, plus
-- a new within-species dimension the original schema did not anticipate:
-- accession identity (Col-0, Ler-0, Cvi-0, … — natural variants of A. thaliana).
--
-- Layout added here:
--   protein_embedding_runs      provenance for each embedding batch (checkpoint,
--                               pooling, layer, sequence source) so a stored
--                               vector is always reproducible/traceable
--   arabidopsis_accessions      accession registry (common_name + external_id +
--                               id_source natural key; one is_reference baseline)
--   proteins.accession_id       nullable FK — cross-species rows stay NULL,
--                               accession rows point at an accession. One locus
--                               per accession enforced by a partial unique index.
--   protein_embeddings_esm3     per-model vector(1536) embeddings (ESM-3),
--                               HNSW cosine-indexed, linked to a run
--
-- The comparison RPCs (knn_search_esm3, compare_gene_across_accessions,
-- search_accession_genes) and the search_genes scoping change live in the
-- sibling migration 20260708000100.
--
-- HNSW (not IVFFLAT) to match protein_embeddings_esm2: IVFFLAT degenerates on
-- the empty table this migration ships (K-means has no vectors to train on) and
-- on tiny test fixtures (each row in its own partition, probes=1 returns only
-- the query). HNSW needs no training step.
--
-- RLS pattern (matching 20260610000000 + 20260622180000):
--   admin_all_<table>   FOR ALL    TO bloom_admin  USING true WITH CHECK true
--   agent_read_<table>  FOR SELECT TO bloom_agent   USING true
--   user_read_<table>   FOR SELECT TO bloom_user    USING true
--   writer_read_<table> FOR SELECT TO bloom_writer  USING true
--   writer_insert_<t>   FOR INSERT TO bloom_writer  WITH CHECK true
--   writer_update_<t>   FOR UPDATE TO bloom_writer  USING true WITH CHECK true
-- Ingest runs as bloom_writer.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS,
-- CREATE INDEX IF NOT EXISTS, DROP POLICY IF EXISTS, ON CONFLICT DO NOTHING.
-- =============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

-- ─── register the ESM-3 model in the existing registry ────────────────────
INSERT INTO public.protein_embedding_models
  (model_id,               display_name,             dimension, table_suffix, description)
VALUES
  ('esm3_open_small_1p4B', 'ESM3-open small (1.4B)',  1536,      'esm3',
   'ESM3-open small protein language model (1.4B params), model dim 1536. Used for single-species Arabidopsis accession comparison.')
ON CONFLICT (model_id) DO NOTHING;

-- ─── protein_embedding_runs (embedding provenance) ────────────────────────
CREATE TABLE IF NOT EXISTS public.protein_embedding_runs (
  id               bigserial   PRIMARY KEY,
  model_id         text        NOT NULL REFERENCES public.protein_embedding_models(model_id),
  checkpoint_hash  text        NOT NULL,
  pooling          text        NOT NULL,
  layer            int,
  sequence_source  text        NOT NULL,
  software_version text,
  computed_at      timestamptz NOT NULL DEFAULT now(),
  notes            text
);

CREATE INDEX IF NOT EXISTS protein_embedding_runs_model_id_idx
  ON public.protein_embedding_runs (model_id);

COMMENT ON TABLE public.protein_embedding_runs IS
  'Provenance for a batch of protein embeddings: exact model checkpoint_hash, pooling (e.g. mean/bos), layer, sequence_source (e.g. Araport11 primary transcript), software_version. A vector(N) is not reproducible or citable without this. Mirrors the versioned orthogroup_runs pattern; every protein_embeddings_<suffix> row references a run.';

-- ─── arabidopsis_accessions (accession registry) ──────────────────────────
CREATE TABLE IF NOT EXISTS public.arabidopsis_accessions (
  id           bigserial   PRIMARY KEY,
  common_name  text        NOT NULL,
  external_id  text        NOT NULL,
  id_source    text        NOT NULL,
  is_reference boolean     NOT NULL DEFAULT false,
  notes        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id_source, external_id)
);

-- At most one designated reference accession (expected: Col-0), the biological
-- baseline compare_gene_across_accessions ranks against by default.
CREATE UNIQUE INDEX IF NOT EXISTS arabidopsis_accessions_one_reference_idx
  ON public.arabidopsis_accessions (is_reference)
  WHERE is_reference = true;

COMMENT ON TABLE public.arabidopsis_accessions IS
  'Registry of Arabidopsis thaliana accessions (natural variants/ecotypes). Natural key (id_source, external_id) — the same numeric ID from two sources stays distinct. common_name is the display label. At most one row may be is_reference=true (the baseline, expected Col-0).';

-- ─── proteins.accession_id (nullable accession dimension) ──────────────────
ALTER TABLE public.proteins
  ADD COLUMN IF NOT EXISTS accession_id bigint REFERENCES public.arabidopsis_accessions(id);

CREATE INDEX IF NOT EXISTS proteins_accession_id_idx
  ON public.proteins (accession_id);

-- One embedding per locus per accession (primary transcript). Cross-species
-- rows (accession_id IS NULL) are exempt and may repeat a gene_id.
CREATE UNIQUE INDEX IF NOT EXISTS proteins_accession_gene_uidx
  ON public.proteins (accession_id, gene_id)
  WHERE accession_id IS NOT NULL;

COMMENT ON COLUMN public.proteins.accession_id IS
  'Nullable FK to arabidopsis_accessions(id). NULL = cross-species protein (ESM-2 surface); NOT NULL = per-accession protein (ESM-3 surface). uid is opaque, keyed on the numeric accession_id (<accession_id>:<gene_id>) and never parsed by application code.';

-- ─── protein_embeddings_esm3 (per-model, provenance-linked) ───────────────
CREATE TABLE IF NOT EXISTS public.protein_embeddings_esm3 (
  uid        text         PRIMARY KEY REFERENCES public.proteins(uid) ON DELETE CASCADE,
  embedding  vector(1536) NOT NULL,
  run_id     bigint       NOT NULL REFERENCES public.protein_embedding_runs(id),
  created_at timestamptz  NOT NULL DEFAULT now(),
  -- Reject the zero vector: NOT NULL does not stop an all-zeros embedding from
  -- a failed inference, which yields an undefined (NaN) cosine distance and
  -- silently corrupts KNN ordering. vector_norm() is the L2 norm for the
  -- `vector` type (l2_norm() covers only halfvec/sparsevec and is ambiguous here).
  CONSTRAINT protein_embeddings_esm3_nonzero_chk CHECK (vector_norm(embedding) > 0)
);

CREATE INDEX IF NOT EXISTS protein_embeddings_esm3_run_id_idx
  ON public.protein_embeddings_esm3 (run_id);

-- HNSW (vs IVFFLAT) — see header. Matches protein_embeddings_esm2.
CREATE INDEX IF NOT EXISTS protein_embeddings_esm3_hnsw_idx
  ON public.protein_embeddings_esm3
  USING hnsw (embedding vector_cosine_ops);

COMMENT ON TABLE public.protein_embeddings_esm3 IS
  'ESM-3 protein embeddings, vector(1536) HNSW cosine-indexed, one row per proteins.uid, linked to a protein_embedding_runs provenance row. Postgres rejects any non-1536 vector at the type-cast boundary (cross-model guardrail); a CHECK rejects the zero vector.';

-- ─── RLS enable ───────────────────────────────────────────────────────────
ALTER TABLE public.protein_embedding_runs    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.arabidopsis_accessions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.protein_embeddings_esm3   ENABLE ROW LEVEL SECURITY;

-- ─── RLS policies: admin_all / agent_read / user_read / writer_* ───────────

-- protein_embedding_runs
DROP POLICY IF EXISTS admin_all_protein_embedding_runs    ON public.protein_embedding_runs;
CREATE POLICY admin_all_protein_embedding_runs
  ON public.protein_embedding_runs FOR ALL    TO bloom_admin  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_protein_embedding_runs   ON public.protein_embedding_runs;
CREATE POLICY agent_read_protein_embedding_runs
  ON public.protein_embedding_runs FOR SELECT TO bloom_agent  USING (true);
DROP POLICY IF EXISTS user_read_protein_embedding_runs    ON public.protein_embedding_runs;
CREATE POLICY user_read_protein_embedding_runs
  ON public.protein_embedding_runs FOR SELECT TO bloom_user   USING (true);
DROP POLICY IF EXISTS writer_read_protein_embedding_runs  ON public.protein_embedding_runs;
CREATE POLICY writer_read_protein_embedding_runs
  ON public.protein_embedding_runs FOR SELECT TO bloom_writer USING (true);
DROP POLICY IF EXISTS writer_insert_protein_embedding_runs ON public.protein_embedding_runs;
CREATE POLICY writer_insert_protein_embedding_runs
  ON public.protein_embedding_runs FOR INSERT TO bloom_writer WITH CHECK (true);
DROP POLICY IF EXISTS writer_update_protein_embedding_runs ON public.protein_embedding_runs;
CREATE POLICY writer_update_protein_embedding_runs
  ON public.protein_embedding_runs FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true);

-- arabidopsis_accessions
DROP POLICY IF EXISTS admin_all_arabidopsis_accessions    ON public.arabidopsis_accessions;
CREATE POLICY admin_all_arabidopsis_accessions
  ON public.arabidopsis_accessions FOR ALL    TO bloom_admin  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_arabidopsis_accessions   ON public.arabidopsis_accessions;
CREATE POLICY agent_read_arabidopsis_accessions
  ON public.arabidopsis_accessions FOR SELECT TO bloom_agent  USING (true);
DROP POLICY IF EXISTS user_read_arabidopsis_accessions    ON public.arabidopsis_accessions;
CREATE POLICY user_read_arabidopsis_accessions
  ON public.arabidopsis_accessions FOR SELECT TO bloom_user   USING (true);
DROP POLICY IF EXISTS writer_read_arabidopsis_accessions  ON public.arabidopsis_accessions;
CREATE POLICY writer_read_arabidopsis_accessions
  ON public.arabidopsis_accessions FOR SELECT TO bloom_writer USING (true);
DROP POLICY IF EXISTS writer_insert_arabidopsis_accessions ON public.arabidopsis_accessions;
CREATE POLICY writer_insert_arabidopsis_accessions
  ON public.arabidopsis_accessions FOR INSERT TO bloom_writer WITH CHECK (true);
DROP POLICY IF EXISTS writer_update_arabidopsis_accessions ON public.arabidopsis_accessions;
CREATE POLICY writer_update_arabidopsis_accessions
  ON public.arabidopsis_accessions FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true);

-- protein_embeddings_esm3
DROP POLICY IF EXISTS admin_all_protein_embeddings_esm3    ON public.protein_embeddings_esm3;
CREATE POLICY admin_all_protein_embeddings_esm3
  ON public.protein_embeddings_esm3 FOR ALL    TO bloom_admin  USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_protein_embeddings_esm3   ON public.protein_embeddings_esm3;
CREATE POLICY agent_read_protein_embeddings_esm3
  ON public.protein_embeddings_esm3 FOR SELECT TO bloom_agent  USING (true);
DROP POLICY IF EXISTS user_read_protein_embeddings_esm3    ON public.protein_embeddings_esm3;
CREATE POLICY user_read_protein_embeddings_esm3
  ON public.protein_embeddings_esm3 FOR SELECT TO bloom_user   USING (true);
DROP POLICY IF EXISTS writer_read_protein_embeddings_esm3  ON public.protein_embeddings_esm3;
CREATE POLICY writer_read_protein_embeddings_esm3
  ON public.protein_embeddings_esm3 FOR SELECT TO bloom_writer USING (true);
DROP POLICY IF EXISTS writer_insert_protein_embeddings_esm3 ON public.protein_embeddings_esm3;
CREATE POLICY writer_insert_protein_embeddings_esm3
  ON public.protein_embeddings_esm3 FOR INSERT TO bloom_writer WITH CHECK (true);
DROP POLICY IF EXISTS writer_update_protein_embeddings_esm3 ON public.protein_embeddings_esm3;
CREATE POLICY writer_update_protein_embeddings_esm3
  ON public.protein_embeddings_esm3 FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true);

-- ─── Table-level GRANTs (PostgREST requires both policy AND grant) ─────────
GRANT SELECT                 ON public.protein_embedding_runs  TO bloom_user, bloom_agent;
GRANT SELECT, INSERT, UPDATE ON public.protein_embedding_runs  TO bloom_writer;
GRANT ALL                    ON public.protein_embedding_runs  TO bloom_admin;
GRANT USAGE, SELECT          ON SEQUENCE public.protein_embedding_runs_id_seq TO bloom_writer, bloom_admin;

GRANT SELECT                 ON public.arabidopsis_accessions  TO bloom_user, bloom_agent;
GRANT SELECT, INSERT, UPDATE ON public.arabidopsis_accessions  TO bloom_writer;
GRANT ALL                    ON public.arabidopsis_accessions  TO bloom_admin;
GRANT USAGE, SELECT          ON SEQUENCE public.arabidopsis_accessions_id_seq TO bloom_writer, bloom_admin;

GRANT SELECT                 ON public.protein_embeddings_esm3 TO bloom_user, bloom_agent;
GRANT SELECT, INSERT, UPDATE ON public.protein_embeddings_esm3 TO bloom_writer;
GRANT ALL                    ON public.protein_embeddings_esm3 TO bloom_admin;

COMMIT;
