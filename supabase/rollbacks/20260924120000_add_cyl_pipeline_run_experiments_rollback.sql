-- Rollback for 20260924120000_add_cyl_pipeline_run_experiments.sql
--
-- Drops the view, then the index. DROP INDEX takes an ACCESS EXCLUSIVE lock on
-- cyl_pipeline_run_scans, so this sets its own lock_timeout and fails fast rather than queueing
-- the poller's and write-back's writes. Nothing else depends on either object: the web UI's
-- experiment panel shows "Runs unavailable" without the view.

BEGIN;

SET LOCAL lock_timeout = '5s';

DROP VIEW IF EXISTS public.cyl_pipeline_run_experiments;

DROP INDEX IF EXISTS public.cyl_pipeline_run_scans_scan_id_idx;

COMMIT;

NOTIFY pgrst, 'reload schema';
