-- Rollback for 20261012230000_let_pipeline_load_default_de.sql
-- Manual break-glass only; nothing runs it automatically. bloom_workflows goes back to reading
-- scrna_de and scrna_cluster_stats only, the step list loses `de`, and service_role may call the
-- run-start function again. DE rows the pipeline already loaded are kept.
--
-- Refuses while a run reports `de`, which the earlier step check doesn't allow.

BEGIN;

DO $$
DECLARE
  n INTEGER;
BEGIN
  SELECT count(*) INTO n FROM public.rnaseq_runs WHERE current_step = 'de';
  IF n > 0 THEN
    RAISE EXCEPTION '% run(s) report de; clear current_step on them first', n;
  END IF;
END
$$;

DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['scrna_de_runs', 'scrna_de', 'scrna_de_genes', 'scrna_cluster_stats']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'workflows_insert_' || t, t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['scrna_de_runs', 'scrna_de_genes']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'workflows_select_' || t, t);
  END LOOP;
END
$$;

REVOKE INSERT ON public.scrna_de_runs, public.scrna_de, public.scrna_de_genes,
  public.scrna_cluster_stats FROM bloom_workflows;
REVOKE SELECT ON public.scrna_de_runs, public.scrna_de_genes FROM bloom_workflows;

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_current_step_check CHECK (
    current_step IS NULL
    OR (workflow_type = 'scrna-cellranger'
        AND current_step IN ('fetch-sra', 'stage-reference', 'download-genome', 'build-reference',
                             'stage', 'qc', 'count', 'preprocess', 'cluster', 'build-h5ad',
                             'load-dataset', 'cleanup'))
);

GRANT EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[], TEXT, JSONB, BIGINT)
    TO service_role;

COMMIT;
