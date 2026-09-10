-- Rollback for 20260825172704_create_scrna_de_runs.sql
--
-- Manual, forward-only companion — not applied by `supabase db push`.
-- Drop order is the reverse of creation: functions, then queue, then runs (which
-- references results), then results, then the role.
--
-- The role is dropped LAST and only if nothing else depends on it. Dropping a role
-- that still owns objects or holds grants elsewhere errors, which is the correct
-- outcome — it means something outside this migration adopted `bloom_scrna_de` and a
-- blind DROP would be destructive.

BEGIN;

DO $$
BEGIN
    ALTER PUBLICATION supabase_realtime DROP TABLE public.scrna_de_runs;
EXCEPTION
    WHEN undefined_object THEN NULL;
END
$$;

DROP FUNCTION IF EXISTS public.update_scrna_de_run_status(BIGINT, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.complete_scrna_de_run(BIGINT, TEXT, INTEGER);
DROP FUNCTION IF EXISTS public.submit_scrna_de_run(BIGINT, BIGINT, TEXT);
DROP FUNCTION IF EXISTS public.claim_scrna_de_run(INTEGER, INTEGER);
DROP FUNCTION IF EXISTS public.fail_scrna_de_run(BIGINT, TEXT, BIGINT);
DROP FUNCTION IF EXISTS public.enqueue_scrna_de_run(BIGINT);

DO $$
BEGIN
    IF EXISTS (SELECT FROM pgmq.list_queues() WHERE queue_name = 'scrna_de_dispatch') THEN
        PERFORM pgmq.drop_queue('scrna_de_dispatch');
    END IF;
END
$$;

DROP POLICY IF EXISTS admin_all_scrna_de_bucket ON storage.objects;
DROP POLICY IF EXISTS scrna_de_read_source_bucket ON storage.objects;
DROP POLICY IF EXISTS scrna_de_update_bucket ON storage.objects;
DROP POLICY IF EXISTS scrna_de_write_bucket ON storage.objects;
DROP POLICY IF EXISTS read_scrna_de_bucket ON storage.objects;

-- The bucket row is dropped only when empty. storage.objects rows are real files in the
-- backing store; deleting the row silently orphans them, so an operator must empty the
-- bucket through the Storage API first. Failing loudly here is the point.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM storage.objects WHERE bucket_id = 'scrna-de') THEN
        RAISE EXCEPTION 'scrna-de bucket is not empty; empty it via the Storage API before rolling back';
    END IF;
    DELETE FROM storage.buckets WHERE id = 'scrna-de';
END
$$;

DROP TABLE IF EXISTS public.scrna_de_runs;
DROP TABLE IF EXISTS public.scrna_de_results;

REVOKE USAGE ON SCHEMA public FROM bloom_scrna_de;
DROP ROLE IF EXISTS bloom_scrna_de;

COMMIT;
