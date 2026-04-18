-- Migration: scrna_cells_add_pcs
-- Phase 1 of Expression Explorer (add-scrna-expression-schema).
-- Adds five nullable REAL columns to scrna_cells for PCA coordinates.
-- Enables the PCA 3D UI tab when populated; UI hides the tab when NULL.
--
-- ALTER TABLE ADD COLUMN ... DEFAULT NULL is metadata-only in Postgres 11+,
-- so no row rewrite occurs even on large existing scrna_cells tables.

BEGIN;

ALTER TABLE public.scrna_cells
  ADD COLUMN IF NOT EXISTS pc1 REAL DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS pc2 REAL DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS pc3 REAL DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS pc4 REAL DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS pc5 REAL DEFAULT NULL;

COMMIT;
