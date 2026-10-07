-- Rollback for 20261005220000_let_pipeline_load_scrna_datasets.sql
-- Manual break-glass only; nothing runs it automatically. Takes back the pipeline's access to
-- scRNA datasets, drops the run's link to its dataset and the link function, and puts
-- scrna_datasets' created_by trigger back on set_created_by(). Datasets already loaded stay.
--
-- Refuses while a run still reports load-dataset, which the earlier step check doesn't allow.

BEGIN;

DO $$
DECLARE
  n INTEGER;
BEGIN
  SELECT count(*) INTO n FROM public.rnaseq_runs WHERE current_step = 'load-dataset';
  IF n > 0 THEN
    RAISE EXCEPTION '% run(s) report load-dataset; clear current_step on them first', n;
  END IF;
END
$$;

DROP FUNCTION IF EXISTS public.link_rnaseq_run_dataset(BIGINT);
-- Its replacement, from 20261007000000, if that wasn't rolled back first.
DROP FUNCTION IF EXISTS public.link_rnaseq_run_dataset(BIGINT, BIGINT);

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_current_step_check CHECK (
    current_step IS NULL
    OR (workflow_type = 'scrna-cellranger'
        AND current_step IN ('fetch-sra', 'stage-reference', 'stage', 'qc', 'count',
                             'preprocess', 'cluster', 'build-h5ad', 'cleanup'))
);

ALTER TABLE public.rnaseq_runs DROP COLUMN IF EXISTS dataset_id;

DROP POLICY IF EXISTS workflows_select_scrna ON storage.objects;
DROP POLICY IF EXISTS workflows_insert_scrna ON storage.objects;
DROP POLICY IF EXISTS workflows_update_scrna ON storage.objects;

DROP POLICY IF EXISTS workflows_insert_scrna_datasets ON public.scrna_datasets;
DROP POLICY IF EXISTS workflows_update_scrna_datasets ON public.scrna_datasets;

DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['species', 'scrna_datasets', 'scrna_clusters', 'scrna_genotypes',
                           'scrna_cells', 'scrna_genes', 'scrna_counts', 'scrna_cluster_stats',
                           'scrna_cluster_neighbors', 'scrna_de']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'workflows_select_' || t, t);
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'workflows_insert_' || t, t);
  END LOOP;
END
$$;

REVOKE EXECUTE ON FUNCTION public.scrna_facets_are_flat_text(JSONB) FROM bloom_workflows;
REVOKE ALL ON public.species, public.scrna_datasets, public.scrna_clusters,
  public.scrna_genotypes, public.scrna_cells, public.scrna_genes, public.scrna_counts,
  public.scrna_cluster_stats, public.scrna_cluster_neighbors, public.scrna_de
  FROM bloom_workflows;

DROP FUNCTION IF EXISTS public.workflows_may_load_scrna_dataset(BIGINT);

DROP TRIGGER IF EXISTS set_created_by_scrna_datasets ON public.scrna_datasets;
CREATE TRIGGER set_created_by_scrna_datasets BEFORE INSERT ON public.scrna_datasets
  FOR EACH ROW EXECUTE FUNCTION public.set_created_by();
DROP FUNCTION IF EXISTS public.set_created_by_as_owner();

COMMIT;
