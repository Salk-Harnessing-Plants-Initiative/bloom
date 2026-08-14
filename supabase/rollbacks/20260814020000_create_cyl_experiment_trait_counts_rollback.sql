-- Manual rollback for 20260814020000_create_cyl_experiment_trait_counts.sql

BEGIN;

DROP FUNCTION IF EXISTS public.refresh_cyl_experiment_trait_counts();
DROP TABLE IF EXISTS public.cyl_experiment_trait_counts;

COMMIT;
