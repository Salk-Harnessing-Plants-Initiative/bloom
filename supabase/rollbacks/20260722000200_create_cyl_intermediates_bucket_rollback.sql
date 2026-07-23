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
--
-- Newer Supabase Storage versions (storage-api >= the release adding
-- migrations/tenant/0055-prevent-direct-deletes.sql; confirmed present in this
-- repo's prod/CI image pin storage-api:v1.48.14, absent in dev's older
-- storage-api:v1.25.7) install a BEFORE DELETE trigger on storage.buckets/
-- storage.objects that raises unless the session-local
-- storage.allow_delete_query setting is 'true' -- a deliberate guard against
-- orphaning S3 bytes via raw SQL. SET LOCAL scopes the exemption to this
-- transaction only; it's a harmless no-op on images without the trigger
-- (Postgres permits setting any dotted/namespaced GUC whether or not an
-- extension has registered it).

BEGIN;

SET LOCAL storage.allow_delete_query = 'true';

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
