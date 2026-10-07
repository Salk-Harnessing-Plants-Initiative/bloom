-- Rollback for 20260929212129_add_scrna_datasets_origin.sql (run by hand).
-- Drops the column and every origin recorded in it.

BEGIN;

ALTER TABLE public.scrna_datasets DROP CONSTRAINT IF EXISTS scrna_datasets_origin_check;
ALTER TABLE public.scrna_datasets DROP COLUMN IF EXISTS origin;

COMMIT;
