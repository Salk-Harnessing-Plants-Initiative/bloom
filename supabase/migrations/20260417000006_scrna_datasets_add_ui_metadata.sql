-- Migration: scrna_datasets_add_ui_metadata
-- Phase 1 of Expression Explorer (add-scrna-expression-schema).
-- Adds nine nullable columns to scrna_datasets:
--   Display:       assay, resolution, n_cells, n_genes, pc_variance (REAL[])
--   Colorbar:      expression_units (documents the normalization applied)
--   Reproducibility: source_checksum (SHA-256 of source per canonical rule),
--                    ingested_at (UTC commit timestamp), markers_method (enum).
--
-- All DEFAULT NULL. ALTER ADD COLUMN DEFAULT NULL is metadata-only in PG11+.
-- Legacy ingest paths that never populate these columns produce rows the UI
-- treats as "provenance not available".

BEGIN;

ALTER TABLE public.scrna_datasets
  ADD COLUMN IF NOT EXISTS assay            TEXT        DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS resolution       REAL        DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_cells          INT         DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_genes          INT         DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS pc_variance      REAL[]      DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS expression_units TEXT        DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS source_checksum  TEXT        DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS ingested_at      TIMESTAMPTZ DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS markers_method   TEXT        DEFAULT NULL;

COMMIT;
