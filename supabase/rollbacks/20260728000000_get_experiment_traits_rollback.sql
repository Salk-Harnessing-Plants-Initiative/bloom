-- Manual rollback for 20260728000000_get_experiment_traits.sql
--
-- Drops the two new functions. Purely additive forward migration, so nothing else to restore:
-- get_scan_traits, cyl_scan_traits_source, cyl_scan_traits_latest, and cyl_scan_trait_names are
-- untouched by the forward migration and remain untouched here.
--
-- ORDER (add-cyl-trait-recipe-key): on a database where
-- 20260929230200_add_cyl_trait_recipe_reads.sql replaced get_experiment_traits with its
-- five-argument form, apply that migration's rollback first. This file drops only the
-- three-argument signature, so on its own it would leave the five-argument function.

BEGIN;

DROP FUNCTION IF EXISTS public.get_experiment_traits(bigint, bigint, text);
DROP FUNCTION IF EXISTS public.list_experiment_trait_sources(bigint);

COMMIT;
