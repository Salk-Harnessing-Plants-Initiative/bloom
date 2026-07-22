-- Rollback for 20260722000200_create_cyl_intermediates_bucket.sql
-- Manual break-glass only: this repo applies migrations forward via `supabase db push`
-- (no automated down-runner). Drops the nine cyl-intermediates storage.objects policies,
-- then the bucket row.
--
-- storage.objects.bucket_id FKs to storage.buckets.id with no cascade, so once the
-- bucket holds even one real object, a plain bucket DELETE fails with a foreign-key
-- violation. Rather than silently deleting uploaded bytes' metadata to force the
-- DROP through, this rollback explicitly REFUSES when the bucket is non-empty —
-- an operator must clear objects out of MinIO/Storage first (a deliberate,
-- reviewed step), not have this script do it for them.

BEGIN;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM storage.objects WHERE bucket_id = 'cyl-intermediates') THEN
    RAISE EXCEPTION
      'cyl-intermediates bucket is non-empty — refusing to drop it. '
      'Delete its objects via the Storage API first, then re-run this rollback.';
  END IF;
END
$$;

DROP POLICY IF EXISTS admin_all_cyl_intermediates ON storage.objects;
DROP POLICY IF EXISTS agent_read_cyl_intermediates ON storage.objects;
DROP POLICY IF EXISTS user_read_cyl_intermediates ON storage.objects;
DROP POLICY IF EXISTS writer_select_cyl_intermediates ON storage.objects;
DROP POLICY IF EXISTS writer_insert_cyl_intermediates ON storage.objects;
DROP POLICY IF EXISTS writer_update_cyl_intermediates ON storage.objects;
DROP POLICY IF EXISTS workflows_select_cyl_intermediates ON storage.objects;
DROP POLICY IF EXISTS workflows_insert_cyl_intermediates ON storage.objects;
DROP POLICY IF EXISTS workflows_update_cyl_intermediates ON storage.objects;

DELETE FROM storage.buckets WHERE id = 'cyl-intermediates';

COMMIT;
