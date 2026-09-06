-- Rollback for 20260818130000_add_cyl_pipeline_run_status_polling.sql
-- Manual break-glass only. Drops the new update_cyl_pipeline_run_status
-- function (its EXECUTE grant goes with it). Leaves cyl_pipeline_runs/
-- cyl_pipeline_run_scans and every Phase 1/2 function untouched — this
-- migration made no table/column/grant changes to any of those.

BEGIN;

DROP FUNCTION IF EXISTS public.update_cyl_pipeline_run_status(BIGINT, TEXT);

COMMIT;
