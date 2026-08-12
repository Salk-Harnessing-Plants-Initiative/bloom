-- Manual rollback for 20260812020000_add_backfill_cyl_scan_traits_is_latest_procedure.sql
--
-- Drops the one new procedure. Purely additive forward migration -- nothing else to restore.

BEGIN;

DROP PROCEDURE IF EXISTS public.backfill_cyl_scan_traits_is_latest(bigint);

COMMIT;
