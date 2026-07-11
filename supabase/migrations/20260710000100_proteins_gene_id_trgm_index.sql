-- Migration: proteins_gene_id_trgm_index
-- Trigram indexes so accession gene/uid substring autocomplete stays fast once
-- proteins holds millions of accession rows. Without these, search_accession_genes'
-- ILIKE '%needle%' scans the whole table and hits the API statement_timeout on a
-- no-match term (e.g. an AT-number).
--
-- Additive only: no existing columns or rows are modified.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS proteins_gene_id_trgm
  ON public.proteins USING gin (gene_id gin_trgm_ops);

CREATE INDEX IF NOT EXISTS proteins_uid_trgm
  ON public.proteins USING gin (uid gin_trgm_ops);

COMMIT;
