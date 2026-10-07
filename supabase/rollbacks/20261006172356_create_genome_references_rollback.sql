-- Rollback for 20261006172356_create_genome_references.sql
-- Manual break-glass only; nothing runs it automatically. Drops the genome tables, functions,
-- triggers and bucket policies, the genome-mkref runs, and restores rnaseq_runs' checks.
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

DROP TRIGGER IF EXISTS finish_genome_reference_build ON public.rnaseq_runs;
DROP FUNCTION IF EXISTS public.finish_genome_reference_build();

DROP TABLE IF EXISTS public.genome_reference_versions;
DROP TABLE IF EXISTS public.genome_references;
DROP FUNCTION IF EXISTS public.genome_reference_versions_guard();
DROP FUNCTION IF EXISTS public.genome_references_keep_identity();

-- Build runs and their queued messages go with the type.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pgmq.list_queues() WHERE queue_name = 'rnaseq_dispatch') THEN
    DELETE FROM pgmq.q_rnaseq_dispatch q
    WHERE q.message ->> 'run_id' IN (
        SELECT id::text FROM public.rnaseq_runs WHERE workflow_type = 'genome-mkref'
    );
  END IF;
END
$$;
DELETE FROM public.rnaseq_runs WHERE workflow_type = 'genome-mkref';

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_genome_mkref_check;

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_workflow_type_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_workflow_type_check
    CHECK (workflow_type IN ('scrna-cellranger'));

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_current_step_check CHECK (
    current_step IS NULL
    OR (workflow_type = 'scrna-cellranger'
        AND current_step IN ('fetch-sra', 'stage-reference', 'stage', 'qc', 'count',
                             'preprocess', 'cluster', 'build-h5ad', 'load-dataset', 'cleanup'))
);

COMMIT;
