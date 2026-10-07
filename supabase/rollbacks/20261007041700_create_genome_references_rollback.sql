-- Rollback for 20261006172356_create_genome_references.sql
-- Manual break-glass only; nothing runs it automatically. Drops the genome tables, functions,
-- triggers, bucket policies and bucket.
--
-- Refuses while the bucket holds any object: storage.objects.bucket_id references the bucket
-- with no cascade, and the files must be deleted through the Storage API first so their bytes
-- don't stay behind in MinIO. SET LOCAL lifts Storage's guard against direct deletes
-- (storage.allow_delete_query) for this transaction only.

BEGIN;

SET LOCAL storage.allow_delete_query = 'true';

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM storage.objects WHERE bucket_id = 'genome-references') THEN
    RAISE EXCEPTION
      'genome-references bucket is non-empty — refusing to drop it. '
      'Delete its objects via the Storage API first, then re-run this rollback.';
  END IF;
END
$$;

DROP POLICY IF EXISTS admin_all_genome_references ON storage.objects;
DROP POLICY IF EXISTS agent_read_genome_references ON storage.objects;
DROP POLICY IF EXISTS user_read_genome_references ON storage.objects;
DROP POLICY IF EXISTS workflows_read_genome_references ON storage.objects;
DROP POLICY IF EXISTS writer_insert_own_genome_upload ON storage.objects;
DROP POLICY IF EXISTS writer_no_genome_update ON storage.objects;
DELETE FROM storage.buckets WHERE id = 'genome-references';

DROP FUNCTION IF EXISTS public.start_genome_version(TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.finish_genome_version(BIGINT, TEXT, BIGINT, TEXT, BIGINT);
DROP FUNCTION IF EXISTS public.abandon_genome_version(BIGINT);
DROP FUNCTION IF EXISTS public._genome_object_bytes(TEXT);

DROP TABLE IF EXISTS public.genome_reference_versions;
DROP TABLE IF EXISTS public.genome_references;
DROP FUNCTION IF EXISTS public.genome_reference_versions_guard();
DROP FUNCTION IF EXISTS public.genome_references_keep_identity();

COMMIT;
