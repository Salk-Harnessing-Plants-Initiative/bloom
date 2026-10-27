-- Rollback for 20260928185707_create_scrna_cellranger_runs.sql (run by hand).
-- Drops the request function, the dispatch queue and the run table; their data is lost.

BEGIN;

DROP FUNCTION IF EXISTS public.request_scrna_cellranger_run(TEXT, TEXT, UUID);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'scrna_cellranger_dispatch') THEN
    PERFORM pgmq.drop_queue('scrna_cellranger_dispatch');
  END IF;
END
$$;

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime DROP TABLE public.scrna_cellranger_runs;
EXCEPTION WHEN undefined_object OR undefined_table THEN
  NULL;
END$$;

DROP TABLE IF EXISTS public.scrna_cellranger_runs;

COMMIT;
