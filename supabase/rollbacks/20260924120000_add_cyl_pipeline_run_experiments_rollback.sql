-- Rollback for 20260924120000_add_cyl_pipeline_run_experiments.sql
--
-- Drops the view, then the index. DROP INDEX takes an ACCESS EXCLUSIVE lock on
-- cyl_pipeline_run_scans, so this sets its own lock_timeout and fails fast rather than queueing
-- the trigger's, poller's and write-back's writes. No database object depends on the view. (The
-- planned web runs panel is specified to show "Runs unavailable" without it.)
--
-- Order: this must run before 20260730120000_create_cyl_pipeline_runs_rollback.sql, which drops
-- two of the view's base tables.

BEGIN;

SET LOCAL lock_timeout = '5s';

DROP VIEW IF EXISTS public.cyl_pipeline_run_experiments;

DROP INDEX IF EXISTS public.cyl_pipeline_run_scans_scan_id_idx;

COMMIT;

NOTIFY pgrst, 'reload schema';
