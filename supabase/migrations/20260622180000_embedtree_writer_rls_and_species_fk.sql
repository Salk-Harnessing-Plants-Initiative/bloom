-- =============================================================================
-- 20260622180000_embedtree_writer_rls_and_species_fk.sql
--
-- Two related embedtree schema fixes:
--
-- 1. bloom_writer RLS + grants on proteins + protein_embeddings_esm2.
--    The original embedtree migration (20260610000000) added admin / agent /
--    user policies but no writer policy, so an authenticated user whose JWT
--    role is `bloom_writer` cannot INSERT/UPDATE. The protein-embedding
--    ingest uploader runs as such a writer, so the missing policy blocks
--    every bulk load via REST.
--
-- 2. proteins.species_id FK to public.species.
--    Other bloom tables (cyl_experiments, assemblies, scrna_datasets,
--    gravi_experiments) link species via `species_id BIGINT REFERENCES
--    species(id)`. proteins.species was created as free-form text and missed
--    the convention. This migration adds the FK column alongside the text
--    column (kept for back-compat — knn_search_esm2 still returns the text
--    species field; a follow-up migration can drop the text column once
--    every caller migrates to JOINing species).
--
-- Idempotent: every CREATE POLICY is preceded by DROP IF EXISTS; the column
-- addition uses IF NOT EXISTS; the constraint name is unique.
-- =============================================================================

BEGIN;

-- ─── 1. bloom_writer RLS policies + grants ────────────────────────────────

-- proteins
DROP POLICY IF EXISTS writer_read_proteins  ON public.proteins;
CREATE POLICY writer_read_proteins
  ON public.proteins FOR SELECT TO bloom_writer USING (true);
DROP POLICY IF EXISTS writer_insert_proteins ON public.proteins;
CREATE POLICY writer_insert_proteins
  ON public.proteins FOR INSERT TO bloom_writer WITH CHECK (true);
DROP POLICY IF EXISTS writer_update_proteins ON public.proteins;
CREATE POLICY writer_update_proteins
  ON public.proteins FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true);

-- protein_embeddings_esm2
DROP POLICY IF EXISTS writer_read_protein_embeddings_esm2   ON public.protein_embeddings_esm2;
CREATE POLICY writer_read_protein_embeddings_esm2
  ON public.protein_embeddings_esm2 FOR SELECT TO bloom_writer USING (true);
DROP POLICY IF EXISTS writer_insert_protein_embeddings_esm2 ON public.protein_embeddings_esm2;
CREATE POLICY writer_insert_protein_embeddings_esm2
  ON public.protein_embeddings_esm2 FOR INSERT TO bloom_writer WITH CHECK (true);
DROP POLICY IF EXISTS writer_update_protein_embeddings_esm2 ON public.protein_embeddings_esm2;
CREATE POLICY writer_update_protein_embeddings_esm2
  ON public.protein_embeddings_esm2 FOR UPDATE TO bloom_writer USING (true) WITH CHECK (true);

-- Table-level GRANTs (RLS policy + GRANT are independent; both are required
-- for PostgREST to expose the table to the bloom_writer role).
GRANT SELECT, INSERT, UPDATE ON public.proteins                TO bloom_writer;
GRANT SELECT, INSERT, UPDATE ON public.protein_embeddings_esm2 TO bloom_writer;

-- ─── 2. proteins.species_id FK to public.species ──────────────────────────

ALTER TABLE public.proteins
  ADD COLUMN IF NOT EXISTS species_id BIGINT REFERENCES public.species(id);

CREATE INDEX IF NOT EXISTS proteins_species_id_idx ON public.proteins (species_id);

COMMENT ON COLUMN public.proteins.species_id IS
  'FK to public.species(id). The canonical structured species link, matching the convention in cyl_experiments / assemblies / scrna_datasets / gravi_experiments. The pre-existing free-form text proteins.species column is kept for back-compat; new code SHOULD JOIN public.species via species_id.';

COMMIT;
