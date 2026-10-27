-- Rollback for 20261020120000_add_pipeline_go_pathways_steps.sql
-- Manual break-glass only; nothing runs it automatically. The step list loses `annotate-genome`
-- and `go-pathways`, and species lose their KEGG codes.
--
-- Refuses while a run reports either step, which the earlier step check doesn't allow.

BEGIN;

DO $$
DECLARE
  n INTEGER;
BEGIN
  SELECT count(*) INTO n FROM public.rnaseq_runs
   WHERE current_step IN ('annotate-genome', 'go-pathways');
  IF n > 0 THEN
    RAISE EXCEPTION '% run(s) report annotate-genome or go-pathways; clear current_step on them first', n;
  END IF;
END
$$;

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_current_step_check CHECK (
    current_step IS NULL
    OR (workflow_type = 'scrna-cellranger'
        AND current_step IN ('fetch-sra', 'stage-reference', 'download-genome', 'build-reference',
                             'stage', 'qc', 'count', 'preprocess', 'cluster', 'de', 'build-h5ad',
                             'load-dataset', 'cleanup'))
);

ALTER TABLE public.species DROP CONSTRAINT IF EXISTS species_kegg_org_check;
ALTER TABLE public.species DROP COLUMN IF EXISTS kegg_org;

COMMIT;
