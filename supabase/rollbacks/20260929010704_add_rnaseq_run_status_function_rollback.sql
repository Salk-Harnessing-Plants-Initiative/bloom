-- Rollback for 20260929010704_add_rnaseq_run_status_function.sql (run by hand).
-- Drops the status poller's function; runs keep the statuses already written.

BEGIN;

DROP FUNCTION IF EXISTS public.update_rnaseq_run_status(BIGINT, TEXT, TEXT, JSONB, INTEGER, TEXT);

COMMIT;
