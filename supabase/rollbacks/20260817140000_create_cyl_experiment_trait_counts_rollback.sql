-- Manual rollback for 20260817140000_create_cyl_experiment_trait_counts.sql
--
-- *** ROLLBACK ORDER: apply 20260817150000's rollback FIRST. ***
-- get_experiment_summary_counts (20260817150000)'s unpinned path reads cyl_experiment_trait_counts
-- from its own PL/pgSQL body -- a reference Postgres's dependency tracker does NOT protect (unlike
-- a view). Dropping this table while that RPC still reads it does not fail at DROP time; the guard
-- below turns that into an immediate, loud error instead -- confirmed via
-- test_rollback_guard_blocks_out_of_order_rollback.

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_proc
        WHERE proname = 'get_experiment_summary_counts'
          AND pronamespace = 'public'::regnamespace
          AND prosrc LIKE '%cyl_experiment_trait_counts%'
    ) THEN
        RAISE EXCEPTION 'Roll back 20260817150000 first -- get_experiment_summary_counts still references cyl_experiment_trait_counts.';
    END IF;
END;
$$;

DROP FUNCTION IF EXISTS public.refresh_cyl_experiment_trait_counts();
DROP TABLE IF EXISTS public.cyl_experiment_trait_counts;

COMMIT;
