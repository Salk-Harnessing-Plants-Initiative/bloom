-- Manual rollback for 20260915120000_refresh_cyl_experiment_trait_counts_incremental.sql
--
-- Unschedules the nightly job and drops the change log, its triggers and the incremental refresh.
-- Leaves the pg_cron extension installed (inert without jobs), and leaves cyl_experiment_trait_counts
-- and refresh_cyl_experiment_trait_counts() untouched.
--
-- ROLLBACK ORDER: roll this back before 20260817140000's rollback, since the incremental refresh
-- writes the cache table that rollback drops.

BEGIN;

DO $$
BEGIN
    IF to_regnamespace('cron') IS NOT NULL THEN
        PERFORM cron.unschedule(jobid)
        FROM cron.job
        WHERE jobname = 'refresh-cyl-experiment-trait-counts';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS mark_cyl_experiment_trait_count_change_on_update
    ON public.cyl_scan_latest_source;
DROP TRIGGER IF EXISTS mark_cyl_experiment_trait_count_change_on_insert_delete
    ON public.cyl_scan_latest_source;
DROP FUNCTION IF EXISTS public.mark_cyl_experiment_trait_count_change();
DROP FUNCTION IF EXISTS public.refresh_changed_cyl_experiment_trait_counts();
DROP TABLE IF EXISTS public.cyl_experiment_trait_count_changes;

COMMIT;
