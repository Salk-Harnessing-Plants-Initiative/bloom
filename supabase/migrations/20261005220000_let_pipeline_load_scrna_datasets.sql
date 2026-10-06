-- 20261005220000_let_pipeline_load_scrna_datasets.sql
--
-- A Cell Ranger run loads its final .h5ad into Bloom as its last step, with
-- `bloomctl scrna hdf5 upload`, signed in as the pipeline account (bloom_workflows). This lets
-- that account load a dataset, and lets the status poller link the run to it afterwards.
--
-- The pipeline writes only datasets it created and has not finished: it creates the dataset,
-- adds its clusters, genotypes, cells, genes and counts, and finishes it. It changes no other
-- dataset and deletes nothing. Once linked, a dataset belongs to the scientist who asked for
-- the run, and the pipeline can no longer write to it.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- 1. created_by without the auth schema ----------------------------------------------------

-- set_created_by() runs as the inserting role and calls auth.uid(), which needs USAGE on the
-- auth schema; only bloom_writer has it (supabase/grants/schema_grants.sql). This copy runs as
-- its owner, so the pipeline's insert works. auth.uid() reads the caller's token either way,
-- so created_by is the same value.
CREATE OR REPLACE FUNCTION public.set_created_by_as_owner()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  NEW.created_by := auth.uid();
  RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.set_created_by_as_owner() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS set_created_by_scrna_datasets ON public.scrna_datasets;
CREATE TRIGGER set_created_by_scrna_datasets BEFORE INSERT ON public.scrna_datasets
  FOR EACH ROW EXECUTE FUNCTION public.set_created_by_as_owner();

-- 2. Which datasets the pipeline may write -------------------------------------------------

-- True for a live dataset the caller created and has not finished. Runs as its owner, because
-- a policy runs as the caller and the pipeline cannot call auth.uid() itself.
CREATE OR REPLACE FUNCTION public.workflows_may_load_scrna_dataset(p_dataset_id BIGINT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.scrna_datasets d
     WHERE d.id = p_dataset_id
       AND d.created_by = auth.uid()
       AND d.ingested_at IS NULL
       AND d.deleted_at IS NULL
  );
$$;

REVOKE EXECUTE ON FUNCTION public.workflows_may_load_scrna_dataset(BIGINT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.workflows_may_load_scrna_dataset(BIGINT) TO bloom_workflows;

-- 3. Table grants --------------------------------------------------------------------------

-- Reads: the species by name, and what a resumed load checks was not built yet.
GRANT SELECT ON public.species, public.scrna_cluster_stats, public.scrna_cluster_neighbors,
  public.scrna_de TO bloom_workflows;

-- Only the columns the load writes, so the pipeline cannot set a dataset's owner, name,
-- species or finish time when creating it, nor change who it belongs to later.
GRANT SELECT ON public.scrna_datasets TO bloom_workflows;
GRANT INSERT (name, species_id, source_checksum, metadata) ON public.scrna_datasets
  TO bloom_workflows;
GRANT UPDATE (n_cells, n_genes, expression_units, metadata, ingested_at) ON public.scrna_datasets
  TO bloom_workflows;

GRANT SELECT, INSERT ON public.scrna_clusters, public.scrna_genotypes, public.scrna_cells,
  public.scrna_genes, public.scrna_counts TO bloom_workflows;

-- scrna_cells' CHECK calls it with the writer's privileges.
GRANT EXECUTE ON FUNCTION public.scrna_facets_are_flat_text(JSONB) TO bloom_workflows;

-- 4. Row policies --------------------------------------------------------------------------

DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['species', 'scrna_datasets', 'scrna_clusters', 'scrna_genotypes',
                           'scrna_cells', 'scrna_genes', 'scrna_counts', 'scrna_cluster_stats',
                           'scrna_cluster_neighbors', 'scrna_de']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'workflows_select_' || t, t);
    EXECUTE format('CREATE POLICY %I ON public.%I FOR SELECT TO bloom_workflows USING (true)',
                   'workflows_select_' || t, t);
  END LOOP;

  FOREACH t IN ARRAY ARRAY['scrna_clusters', 'scrna_genotypes', 'scrna_cells', 'scrna_genes',
                           'scrna_counts']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'workflows_insert_' || t, t);
    EXECUTE format(
      'CREATE POLICY %I ON public.%I FOR INSERT TO bloom_workflows '
      'WITH CHECK (public.workflows_may_load_scrna_dataset(dataset_id))',
      'workflows_insert_' || t, t);
  END LOOP;
END
$$;

DROP POLICY IF EXISTS workflows_insert_scrna_datasets ON public.scrna_datasets;
CREATE POLICY workflows_insert_scrna_datasets ON public.scrna_datasets
  FOR INSERT TO bloom_workflows
  WITH CHECK (true);

-- Finishing sets ingested_at, so the new row is not checked against the unfinished rule; the
-- column grants above already limit what changes.
DROP POLICY IF EXISTS workflows_update_scrna_datasets ON public.scrna_datasets;
CREATE POLICY workflows_update_scrna_datasets ON public.scrna_datasets
  FOR UPDATE TO bloom_workflows
  USING (public.workflows_may_load_scrna_dataset(id))
  WITH CHECK (true);

-- 5. Storage -------------------------------------------------------------------------------

-- The load stores the file under h5ad/ and each gene's counts under counts/, replacing a
-- counts file a stopped load left. An upsert reads the object back, so SELECT too.
DROP POLICY IF EXISTS workflows_select_scrna ON storage.objects;
CREATE POLICY workflows_select_scrna ON storage.objects
    FOR SELECT TO bloom_workflows
    USING (bucket_id = 'scrna');

DROP POLICY IF EXISTS workflows_insert_scrna ON storage.objects;
CREATE POLICY workflows_insert_scrna ON storage.objects
    FOR INSERT TO bloom_workflows
    WITH CHECK (bucket_id = 'scrna' AND (name LIKE 'h5ad/%' OR name LIKE 'counts/%'));

DROP POLICY IF EXISTS workflows_update_scrna ON storage.objects;
CREATE POLICY workflows_update_scrna ON storage.objects
    FOR UPDATE TO bloom_workflows
    USING (bucket_id = 'scrna' AND (name LIKE 'h5ad/%' OR name LIKE 'counts/%'))
    WITH CHECK (bucket_id = 'scrna' AND (name LIKE 'h5ad/%' OR name LIKE 'counts/%'));

-- 6. The run's dataset ---------------------------------------------------------------------

ALTER TABLE public.rnaseq_runs ADD COLUMN IF NOT EXISTS dataset_id BIGINT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conname = 'rnaseq_runs_dataset_id_fkey'
       AND conrelid = 'public.rnaseq_runs'::regclass
  ) THEN
    ALTER TABLE public.rnaseq_runs
      ADD CONSTRAINT rnaseq_runs_dataset_id_fkey
      FOREIGN KEY (dataset_id) REFERENCES public.scrna_datasets (id) ON DELETE SET NULL;
  END IF;
END
$$;

-- One run per dataset.
DO $$
DECLARE
  existing TEXT;
BEGIN
  SELECT pg_get_constraintdef(oid) INTO existing
    FROM pg_constraint
   WHERE conname = 'rnaseq_runs_dataset_id_key'
     AND conrelid = 'public.rnaseq_runs'::regclass;
  IF existing IS NULL THEN
    ALTER TABLE public.rnaseq_runs
      ADD CONSTRAINT rnaseq_runs_dataset_id_key UNIQUE (dataset_id);
  ELSIF existing <> 'UNIQUE (dataset_id)' THEN
    RAISE EXCEPTION 'rnaseq_runs_dataset_id_key is %, expected UNIQUE (dataset_id)', existing;
  END IF;
END
$$;

COMMENT ON COLUMN public.rnaseq_runs.dataset_id IS
  'The scRNA dataset the run loaded, set by link_rnaseq_run_dataset once its load-dataset '
  'step succeeds.';

-- 7. The step a run reports while it loads -------------------------------------------------

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_current_step_check CHECK (
    current_step IS NULL
    OR (workflow_type = 'scrna-cellranger'
        AND current_step IN ('fetch-sra', 'stage-reference', 'stage', 'qc', 'count',
                             'preprocess', 'cluster', 'build-h5ad', 'load-dataset', 'cleanup'))
);

-- 8. Linking a run to its dataset ----------------------------------------------------------

-- Called by the status poller once a run's load-dataset step succeeds. Finds the finished
-- dataset the pipeline loaded under the run's species and dataset name, records it on the
-- run, copies the form's source details into it, and gives it to the scientist who asked for
-- the run. Linking again returns the same dataset. Returns the dataset id.
CREATE OR REPLACE FUNCTION public.link_rnaseq_run_dataset(p_run_id BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_run public.rnaseq_runs%ROWTYPE;
  v_species BIGINT;
  v_name TEXT;
  v_ids BIGINT[];
  v_dataset public.scrna_datasets%ROWTYPE;
  v_linked BIGINT;
  v_source JSONB;
BEGIN
  IF p_run_id IS NULL THEN
    RAISE EXCEPTION 'run id is required' USING ERRCODE = '22023';
  END IF;

  SELECT * INTO v_run FROM public.rnaseq_runs WHERE id = p_run_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no run %', p_run_id USING ERRCODE = 'P0002';
  END IF;
  IF v_run.dataset_id IS NOT NULL THEN
    RETURN v_run.dataset_id;
  END IF;
  IF v_run.workflow_type <> 'scrna-cellranger' THEN
    RAISE EXCEPTION 'run % is not a Cell Ranger run', p_run_id USING ERRCODE = '22023';
  END IF;
  IF v_run.status NOT IN ('running', 'succeeded') THEN
    RAISE EXCEPTION 'run % is %, so its load has not succeeded', p_run_id, v_run.status
      USING ERRCODE = '55000';
  END IF;
  IF jsonb_typeof(v_run.metadata -> 'species_id') IS DISTINCT FROM 'number'
     OR jsonb_typeof(v_run.metadata -> 'dataset_name') IS DISTINCT FROM 'string' THEN
    RAISE EXCEPTION 'run % has no species and dataset name', p_run_id USING ERRCODE = '22023';
  END IF;
  v_species := (v_run.metadata ->> 'species_id')::BIGINT;
  v_name := btrim(v_run.metadata ->> 'dataset_name');

  -- bloomctl stores the trimmed name, and refuses one that differs from a live dataset's only
  -- in capitals, so the trimmed name finds the dataset it loaded.
  SELECT array_agg(d.id) INTO v_ids
    FROM public.scrna_datasets d
   WHERE d.species_id = v_species AND btrim(d.name) = v_name AND d.deleted_at IS NULL;
  IF v_ids IS NULL THEN
    RAISE EXCEPTION 'no dataset named % for species %', v_name, v_species USING ERRCODE = 'P0002';
  END IF;
  IF cardinality(v_ids) > 1 THEN
    RAISE EXCEPTION '% datasets are named % for species %', cardinality(v_ids), v_name, v_species
      USING ERRCODE = '21000';
  END IF;

  SELECT * INTO v_dataset FROM public.scrna_datasets WHERE id = v_ids[1] FOR UPDATE;
  IF v_dataset.ingested_at IS NULL THEN
    RAISE EXCEPTION 'dataset % is not finished loading', v_dataset.id USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM auth.users u
     WHERE u.id = v_dataset.created_by
       AND coalesce((u.raw_app_meta_data ->> 'is_workflows')::BOOLEAN, false)
  ) THEN
    RAISE EXCEPTION 'dataset % was not loaded by the pipeline', v_dataset.id
      USING ERRCODE = '42501';
  END IF;
  SELECT id INTO v_linked FROM public.rnaseq_runs WHERE dataset_id = v_dataset.id;
  IF FOUND THEN
    RAISE EXCEPTION 'dataset % is already linked to run %', v_dataset.id, v_linked
      USING ERRCODE = '23505';
  END IF;

  v_source := jsonb_strip_nulls(jsonb_build_object(
    'origin', v_run.metadata -> 'origin',
    'accession', v_run.metadata -> 'accession',
    'experiment_name', v_run.metadata -> 'experiment_name',
    'citation', v_run.metadata -> 'citation',
    'attributes', v_run.metadata -> 'attributes',
    'rnaseq_run_id', p_run_id
  ));

  UPDATE public.scrna_datasets
     SET url = coalesce(nullif(btrim(v_run.metadata ->> 'source_url'), ''), url),
         metadata = coalesce(metadata, '{}'::JSONB) || jsonb_build_object('source', v_source),
         created_by = v_run.requested_by
   WHERE id = v_dataset.id;

  UPDATE public.rnaseq_runs SET dataset_id = v_dataset.id WHERE id = p_run_id;

  RETURN v_dataset.id;
END;
$$;

-- Only bloom_workflows may call it.
REVOKE EXECUTE ON FUNCTION public.link_rnaseq_run_dataset(BIGINT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.link_rnaseq_run_dataset(BIGINT) TO bloom_workflows;

COMMIT;
