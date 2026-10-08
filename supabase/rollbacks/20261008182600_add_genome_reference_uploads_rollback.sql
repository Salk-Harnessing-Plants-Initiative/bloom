-- Rollback for 20261008182600_add_genome_reference_uploads.sql
-- Manual break-glass only; nothing runs it automatically. Drops rnaseq_runs.genome_version_id
-- (which erases which genome version each run used), the upload functions, and the
-- genome-references bucket and its policies.
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

DROP TRIGGER IF EXISTS rnaseq_runs_keep_genome_version ON public.rnaseq_runs;
DROP FUNCTION IF EXISTS public.rnaseq_runs_keep_genome_version();
DROP INDEX IF EXISTS public.rnaseq_runs_genome_version_id_idx;
ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_genome_version_id_fkey;
ALTER TABLE public.rnaseq_runs DROP COLUMN IF EXISTS genome_version_id;

DROP POLICY IF EXISTS admin_all_genome_references_objects ON storage.objects;
DROP POLICY IF EXISTS agent_read_genome_references_objects ON storage.objects;
DROP POLICY IF EXISTS user_read_genome_references_objects ON storage.objects;
DROP POLICY IF EXISTS workflows_read_genome_references_objects ON storage.objects;
DROP POLICY IF EXISTS writer_insert_own_genome_upload ON storage.objects;
DROP POLICY IF EXISTS writer_no_genome_update ON storage.objects;
DROP POLICY IF EXISTS admin_keep_finished_genome_files_update ON storage.objects;
DROP POLICY IF EXISTS admin_keep_finished_genome_files_delete ON storage.objects;
DELETE FROM storage.buckets WHERE id = 'genome-references';

DROP FUNCTION IF EXISTS public.start_genome_version(TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT);
DROP FUNCTION IF EXISTS public.finish_genome_version(BIGINT, TEXT, BIGINT, TEXT, BIGINT);
DROP FUNCTION IF EXISTS public.abandon_genome_version(BIGINT);
DROP FUNCTION IF EXISTS public._genome_object_bytes(TEXT);

COMMIT;
