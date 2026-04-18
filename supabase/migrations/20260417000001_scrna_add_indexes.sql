-- Migration: scrna_add_indexes
-- Phase 1 of Expression Explorer (add-scrna-expression-schema).
-- Adds indexes to all scrna_* tables for dataset-scoped queries and
-- enables pg_trgm for gene-name autocomplete.
--
-- Additive only: no existing columns or rows are modified.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_scrna_cells_dataset_id
  ON public.scrna_cells (dataset_id);

CREATE INDEX IF NOT EXISTS idx_scrna_cells_dataset_cluster
  ON public.scrna_cells (dataset_id, cluster_id);

CREATE INDEX IF NOT EXISTS idx_scrna_genes_dataset_id
  ON public.scrna_genes (dataset_id);

CREATE INDEX IF NOT EXISTS idx_scrna_genes_gene_name_trgm
  ON public.scrna_genes USING gin (gene_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_scrna_counts_dataset_gene
  ON public.scrna_counts (dataset_id, gene_id);

CREATE INDEX IF NOT EXISTS idx_scrna_de_dataset_cluster
  ON public.scrna_de (dataset_id, cluster_id);

COMMIT;
