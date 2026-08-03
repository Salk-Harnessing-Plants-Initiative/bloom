-- Rollback for 20260730120000_create_cyl_pipeline_runs.sql
-- Manual break-glass only: this repo applies migrations forward via `supabase db push`
-- (no automated down-runner). Drops both new tables (policies, constraints, and
-- table-level GRANTs go with them — greenfield leaf tables, nothing references them),
-- drops the enqueue function and its EXECUTE grant, drops the pgmq queue, removes
-- both tables from the Realtime publication, and reverts the new read policy +
-- column-scoped grants added to the EXISTING cyl_scan_traits/cyl_trait_sources
-- tables. Any data in the two new tables is lost.

BEGIN;

-- Revert the new bloom_workflows access on EXISTING tables first (order matters
-- only for readability here — none of these statements depend on each other).
REVOKE SELECT (scan_id, source_id) ON public.cyl_scan_traits FROM bloom_workflows;
REVOKE SELECT (id, metadata) ON public.cyl_trait_sources FROM bloom_workflows;
REVOKE SELECT (id) ON public.cyl_waves FROM bloom_workflows;
REVOKE SELECT (id) ON public.cyl_experiments FROM bloom_workflows;
DROP POLICY IF EXISTS workflows_read_cyl_scan_traits ON public.cyl_scan_traits;
DROP POLICY IF EXISTS workflows_read_cyl_trait_sources ON public.cyl_trait_sources;
DROP POLICY IF EXISTS workflows_read_cyl_waves ON public.cyl_waves;
DROP POLICY IF EXISTS workflows_read_cyl_experiments ON public.cyl_experiments;

-- Drop the enqueue function (its grants are dropped with it) and the queue.
DROP FUNCTION IF EXISTS public.enqueue_cyl_pipeline_batch(BIGINT, INTEGER, BIGINT[]);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'cyl_pipeline_dispatch') THEN
    PERFORM pgmq.drop_queue('cyl_pipeline_dispatch');
  END IF;
END
$$;

-- Remove from the Realtime publication (harmless if the table is already gone,
-- but done before the DROP TABLE for clarity).
DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime DROP TABLE public.cyl_pipeline_run_scans;
EXCEPTION WHEN undefined_object THEN
  NULL;
END$$;

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime DROP TABLE public.cyl_pipeline_runs;
EXCEPTION WHEN undefined_object THEN
  NULL;
END$$;

-- Drop the two new tables. cyl_pipeline_run_scans references cyl_pipeline_runs,
-- so it must drop first.
DROP TABLE IF EXISTS cyl_pipeline_run_scans;
DROP TABLE IF EXISTS cyl_pipeline_runs;

COMMIT;
