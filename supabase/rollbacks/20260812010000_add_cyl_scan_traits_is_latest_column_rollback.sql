-- Manual rollback for 20260812010000_add_cyl_scan_traits_is_latest_column.sql
--
-- Drops the trigger, then its function, then the index, then the column, in that order -- no
-- CASCADE (design.md's Rollback Ordering rule): if a later migration (the view cutover, M4) still
-- depends on this column, this MUST fail loudly, not silently cascade through a dependent view.

BEGIN;

DROP TRIGGER IF EXISTS maintain_is_latest_after_write ON public.cyl_scan_traits;
DROP FUNCTION IF EXISTS public.maintain_cyl_scan_traits_is_latest();
DROP INDEX IF EXISTS public.idx_cyl_scan_traits_latest;
ALTER TABLE public.cyl_scan_traits DROP COLUMN IF EXISTS is_latest;

COMMIT;
