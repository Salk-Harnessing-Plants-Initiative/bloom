-- Manual rollback for 20260807000000_get_experiment_summary_counts.sql
--
-- Drops the one new function. Purely additive forward migration, so nothing else to restore:
-- get_experiment_traits, get_scan_traits, list_experiment_trait_sources,
-- cyl_scan_traits_source, and cyl_scan_traits_latest are untouched by the forward migration and
-- remain untouched here.

BEGIN;

DROP FUNCTION IF EXISTS public.get_experiment_summary_counts(bigint, bigint, text);

COMMIT;
