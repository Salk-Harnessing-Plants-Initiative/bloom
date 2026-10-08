-- Rollback for 20261008230000_add_scrna_cluster_cell_types.sql
-- Manual break-glass only; nothing runs it automatically. Drops the predicted cell types.

BEGIN;

DROP TABLE IF EXISTS public.scrna_cluster_cell_types;

COMMIT;
