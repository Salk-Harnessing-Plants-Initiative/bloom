-- Rollback for 20261007000000_link_rnaseq_run_by_dataset_id.sql
-- Manual break-glass only; nothing runs it automatically. Puts back the link function that
-- finds the run's dataset by the name on its form. Links already made stay.

BEGIN;

DROP FUNCTION IF EXISTS public.link_rnaseq_run_dataset(BIGINT, BIGINT);

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
  IF v_run.status = 'failed' THEN
    RAISE EXCEPTION
      'run % failed; contact the Bloom team to remove its incomplete dataset, then start the run again',
      p_run_id USING ERRCODE = '55000';
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

COMMENT ON COLUMN public.rnaseq_runs.dataset_id IS
  'The scRNA dataset the run loaded, set by link_rnaseq_run_dataset once its load-dataset '
  'step succeeds.';

COMMIT;
