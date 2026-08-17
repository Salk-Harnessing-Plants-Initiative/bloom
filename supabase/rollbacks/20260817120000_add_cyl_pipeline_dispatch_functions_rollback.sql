-- Rollback for 20260813120000_add_cyl_pipeline_dispatch_functions.sql
-- Manual break-glass only. Drops the three new wrapper functions (their
-- EXECUTE grants go with them) and the private _settle_cyl_pipeline_run
-- helper. Leaves Phase 1's cyl_pipeline_runs/cyl_pipeline_run_scans tables,
-- the cyl_pipeline_dispatch queue, and enqueue_cyl_pipeline_batch untouched —
-- this migration made no table/column/grant changes to any of those.

BEGIN;

DROP FUNCTION IF EXISTS public.claim_cyl_pipeline_batch(INTEGER, INTEGER);
DROP FUNCTION IF EXISTS public.complete_cyl_pipeline_batch(BIGINT, INTEGER, BIGINT, BIGINT[], TEXT);
DROP FUNCTION IF EXISTS public.fail_cyl_pipeline_batch(BIGINT, INTEGER, BIGINT, BIGINT[], TEXT);
DROP FUNCTION IF EXISTS public._settle_cyl_pipeline_run(BIGINT);

COMMIT;
