-- 20261012230000_let_pipeline_load_default_de.sql
--
-- The Cell Ranger pipeline loads each run's default differential expression (every cluster
-- against the rest) with its dataset: bloom_workflows may add DE runs, comparisons and genes,
-- and each cluster's statistics, for a dataset it is still loading, under the same rule as the
-- rest of the load (workflows_may_load_scrna_dataset). It changes and deletes none of them.
-- A run's current step may be `de`. The run-start function is closed to service_role, so only
-- bloom_workflows may start a run.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- 1. Table grants --------------------------------------------------------------------------

GRANT SELECT, INSERT ON public.scrna_de_runs, public.scrna_de, public.scrna_de_genes,
  public.scrna_cluster_stats TO bloom_workflows;

-- 2. Row policies --------------------------------------------------------------------------

DO $$
DECLARE
  t TEXT;
BEGIN
  -- scrna_de and scrna_cluster_stats already have their read rule (20261005220000).
  FOREACH t IN ARRAY ARRAY['scrna_de_runs', 'scrna_de_genes']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'workflows_select_' || t, t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO bloom_workflows USING (true)',
                   'workflows_select_' || t, t);
  END LOOP;

  FOREACH t IN ARRAY ARRAY['scrna_de_runs', 'scrna_de', 'scrna_de_genes', 'scrna_cluster_stats']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'workflows_insert_' || t, t);
    EXECUTE format(
      'CREATE POLICY %I ON public.%I FOR INSERT TO bloom_workflows '
      'WITH CHECK (public.workflows_may_load_scrna_dataset(dataset_id))',
      'workflows_insert_' || t, t);
  END LOOP;
END
$$;

-- 3. The steps a run reports ---------------------------------------------------------------

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_current_step_check CHECK (
    current_step IS NULL
    OR (workflow_type = 'scrna-cellranger'
        AND current_step IN ('fetch-sra', 'stage-reference', 'download-genome', 'build-reference',
                             'stage', 'qc', 'count', 'preprocess', 'cluster', 'de', 'build-h5ad',
                             'load-dataset', 'cleanup'))
);

-- 4. Who may start a run -------------------------------------------------------------------

REVOKE EXECUTE ON FUNCTION public.request_scrna_cellranger_run(TEXT, TEXT, UUID, JSONB, TEXT[], TEXT, JSONB, BIGINT)
    FROM service_role;

COMMIT;
