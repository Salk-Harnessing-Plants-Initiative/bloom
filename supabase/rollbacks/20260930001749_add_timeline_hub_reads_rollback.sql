-- Rollback for 20260930001749_add_timeline_hub_reads.sql (run by hand).
-- Drops the plate timeline view and the requester lookup; no data is stored in either.

BEGIN;

DROP FUNCTION IF EXISTS public.rnaseq_run_requesters(BIGINT[]);
DROP VIEW IF EXISTS public.gravi_scan_timeline;

COMMIT;
