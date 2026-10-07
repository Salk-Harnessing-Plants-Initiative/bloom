-- 20261007000000_link_rnaseq_run_by_dataset_id.sql
--
-- link_rnaseq_run_dataset now takes the id of the dataset the run's load-dataset step created,
-- rather than finding one by the name typed on the run's form. The load step may load under a
-- versioned name (Root atlas_v2) when the typed one is taken, and a name never says which run
-- loaded a dataset; the id does. The function still links only a finished dataset the pipeline
-- loaded, of the run's species, that no other run has.
--
-- Replaces the one-argument function, so there is still one link function.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

DROP FUNCTION IF EXISTS public.link_rnaseq_run_dataset(BIGINT);

-- Called by the status poller once a run's load-dataset step succeeds, with the dataset that
-- step reports. Records it on the run, copies the form's source details into it, and gives it
-- to the scientist who asked for the run. Linking again returns the run's dataset.
CREATE OR REPLACE FUNCTION public.link_rnaseq_run_dataset(p_run_id BIGINT, p_dataset_id BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_run public.rnaseq_runs%ROWTYPE;
  v_species BIGINT;
  v_dataset public.scrna_datasets%ROWTYPE;
  v_linked BIGINT;
  v_source JSONB;
BEGIN
  IF p_run_id IS NULL OR p_dataset_id IS NULL THEN
    RAISE EXCEPTION 'run id and dataset id are required' USING ERRCODE = '22023';
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

  SELECT * INTO v_dataset FROM public.scrna_datasets
   WHERE id = p_dataset_id AND deleted_at IS NULL
     FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no dataset %', p_dataset_id USING ERRCODE = 'P0002';
  END IF;
  IF v_dataset.species_id IS DISTINCT FROM v_species THEN
    RAISE EXCEPTION 'dataset % is not of run %''s species', v_dataset.id, p_run_id
      USING ERRCODE = '22023';
  END IF;
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

REVOKE EXECUTE ON FUNCTION public.link_rnaseq_run_dataset(BIGINT, BIGINT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.link_rnaseq_run_dataset(BIGINT, BIGINT) TO bloom_workflows;

COMMENT ON COLUMN public.rnaseq_runs.dataset_id IS
  'The scRNA dataset the run loaded, set by link_rnaseq_run_dataset with the dataset its '
  'load-dataset step reports.';

COMMIT;
