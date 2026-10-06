-- Rollback for 20261005184138_create_run_logs_bucket.sql
-- Manual break-glass only; nothing runs it automatically. Drops the six run-logs
-- storage.objects policies, then the bucket row.
--
-- Refuses while the bucket holds any object: storage.objects.bucket_id references the bucket
-- with no cascade, and the logs must be deleted through the Storage API first so their bytes
-- don't stay behind in MinIO. SET LOCAL lifts Storage's guard against direct deletes
-- (storage.allow_delete_query) for this transaction only.

BEGIN;

SET LOCAL storage.allow_delete_query = 'true';

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM storage.objects WHERE bucket_id = 'run-logs') THEN
    RAISE EXCEPTION
      'run-logs bucket is non-empty — refusing to drop it. '
      'Delete its objects via the Storage API first, then re-run this rollback.';
  END IF;
END
$$;

DROP POLICY IF EXISTS admin_all_run_logs ON storage.objects;
DROP POLICY IF EXISTS agent_read_run_logs ON storage.objects;
DROP POLICY IF EXISTS user_read_run_logs ON storage.objects;
DROP POLICY IF EXISTS workflows_select_run_logs ON storage.objects;
DROP POLICY IF EXISTS workflows_insert_run_logs ON storage.objects;
DROP POLICY IF EXISTS workflows_update_run_logs ON storage.objects;

DELETE FROM storage.buckets WHERE id = 'run-logs';

COMMIT;
