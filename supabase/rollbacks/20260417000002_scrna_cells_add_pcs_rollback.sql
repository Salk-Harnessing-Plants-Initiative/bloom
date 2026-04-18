-- Rollback for 20260417000002_scrna_cells_add_pcs.sql
-- Drops the five PCA columns from scrna_cells. Data in these columns is lost.

BEGIN;

ALTER TABLE public.scrna_cells
  DROP COLUMN IF EXISTS pc1,
  DROP COLUMN IF EXISTS pc2,
  DROP COLUMN IF EXISTS pc3,
  DROP COLUMN IF EXISTS pc4,
  DROP COLUMN IF EXISTS pc5;

COMMIT;
