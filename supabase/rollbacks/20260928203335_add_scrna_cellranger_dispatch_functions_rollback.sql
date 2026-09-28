-- Rollback for 20260928203335_add_scrna_cellranger_dispatch_functions.sql (run by hand).
-- Drops the three dispatch functions; runs and queue messages are kept.

BEGIN;

DROP FUNCTION IF EXISTS public.claim_scrna_cellranger_run(INTEGER, INTEGER);
DROP FUNCTION IF EXISTS public.complete_scrna_cellranger_run(BIGINT, BIGINT, TEXT);
DROP FUNCTION IF EXISTS public.fail_scrna_cellranger_run(BIGINT, BIGINT, TEXT);

COMMIT;
