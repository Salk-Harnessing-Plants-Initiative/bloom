-- Rollback for 20260417000006_scrna_datasets_add_ui_metadata.sql
-- Drops the nine added columns. Data in these columns is lost.

BEGIN;

ALTER TABLE public.scrna_datasets
  DROP COLUMN IF EXISTS assay,
  DROP COLUMN IF EXISTS resolution,
  DROP COLUMN IF EXISTS n_cells,
  DROP COLUMN IF EXISTS n_genes,
  DROP COLUMN IF EXISTS pc_variance,
  DROP COLUMN IF EXISTS expression_units,
  DROP COLUMN IF EXISTS source_checksum,
  DROP COLUMN IF EXISTS ingested_at,
  DROP COLUMN IF EXISTS markers_method;

COMMIT;
