-- Manual rollback for 20260814020000_create_cyl_experiment_trait_counts.sql
--
-- *** ROLLBACK ORDER: apply 20260814030000's rollback FIRST. ***
-- get_experiment_summary_counts (20260814030000)'s unpinned path reads cyl_experiment_trait_counts
-- from its own PL/pgSQL body -- a reference Postgres's dependency tracker does NOT protect (unlike
-- a view). Dropping this table while that RPC still reads it does not fail at DROP time; every
-- subsequent unpinned call to get_experiment_summary_counts fails instead, at runtime, with
-- "relation cyl_experiment_trait_counts does not exist" -- confirmed via
-- test_rolling_back_out_of_order_breaks_get_experiment_summary_counts.

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_proc
        WHERE proname = 'get_experiment_summary_counts'
          AND prosrc LIKE '%cyl_experiment_trait_counts%'
    ) THEN
        RAISE EXCEPTION 'Roll back 20260814030000 first -- get_experiment_summary_counts still references cyl_experiment_trait_counts.';
    END IF;
END;
$$;

DROP FUNCTION IF EXISTS public.refresh_cyl_experiment_trait_counts();
DROP TABLE IF EXISTS public.cyl_experiment_trait_counts;

COMMIT;
