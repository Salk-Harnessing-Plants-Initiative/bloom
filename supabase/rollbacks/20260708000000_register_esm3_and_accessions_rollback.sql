-- Manual rollback for 20260708000000_register_esm3_and_accessions.sql
--
-- Drops the accession embedding layer in explicit FK order:
--   protein_embeddings_esm3 → proteins.accession_id (+ indexes) →
--   arabidopsis_accessions → protein_embedding_runs → the esm3 registry row.
-- Apply the RPC rollback (20260708000100_..._rollback.sql) FIRST — its
-- functions reference these objects.
--
-- Dropping proteins.accession_id is safe: all cross-species rows have it NULL,
-- and no accession rows exist unless data was loaded (drop those first if so).

BEGIN;

DROP TABLE IF EXISTS public.protein_embeddings_esm3;

DROP INDEX IF EXISTS public.proteins_accession_gene_uidx;
DROP INDEX IF EXISTS public.proteins_accession_id_idx;
ALTER TABLE public.proteins DROP COLUMN IF EXISTS accession_id;

DROP TABLE IF EXISTS public.arabidopsis_accessions;
DROP TABLE IF EXISTS public.protein_embedding_runs;

DELETE FROM public.protein_embedding_models
 WHERE model_id = 'esm3_open_small_1p4B';

COMMIT;
