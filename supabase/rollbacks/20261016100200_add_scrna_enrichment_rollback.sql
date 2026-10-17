-- Rollback for 20261016100200_add_scrna_enrichment.sql
-- Manual break-glass only; nothing runs it automatically. Drops scrna_enrichment_runs (which
-- forgets every enrichment file; delete them under scrna/enrichment/ through the Storage API)
-- and scrna_datasets.genome_version_id (which erases which genome version each dataset was
-- mapped to). Run the rollback of 20261016100300 first.

BEGIN;

-- link_rnaseq_run_dataset as 20261007000000 left it, without the dataset's genome version.
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
  IF v_run.dataset_id = p_dataset_id THEN
    RETURN v_run.dataset_id;
  END IF;
  IF v_run.dataset_id IS NOT NULL THEN
    RAISE EXCEPTION 'run % is already linked to dataset %', p_run_id, v_run.dataset_id
      USING ERRCODE = '23505';
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
  IF (v_dataset.metadata ->> 'rnaseq_run_id') IS DISTINCT FROM p_run_id::TEXT THEN
    RAISE EXCEPTION 'dataset % was not loaded for run %', v_dataset.id, p_run_id
      USING ERRCODE = '42501';
  END IF;
  IF v_dataset.ingested_at IS NULL THEN
    RAISE EXCEPTION 'dataset % is not finished loading', v_dataset.id USING ERRCODE = '55000';
  END IF;
  -- Before the owner check: a dataset another run took is no longer the pipeline's.
  SELECT id INTO v_linked FROM public.rnaseq_runs WHERE dataset_id = v_dataset.id;
  IF FOUND THEN
    RAISE EXCEPTION 'dataset % is already linked to run %', v_dataset.id, v_linked
      USING ERRCODE = '23505';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM auth.users u
     WHERE u.id = v_dataset.created_by
       AND coalesce((u.raw_app_meta_data ->> 'is_workflows')::BOOLEAN, false)
  ) THEN
    RAISE EXCEPTION 'dataset % was not loaded by the pipeline', v_dataset.id
      USING ERRCODE = '42501';
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

DROP TABLE IF EXISTS public.scrna_enrichment_runs;
DROP FUNCTION IF EXISTS public.scrna_enrichment_runs_guard();

DROP INDEX IF EXISTS public.scrna_datasets_genome_version_id_idx;
ALTER TABLE public.scrna_datasets DROP CONSTRAINT IF EXISTS scrna_datasets_genome_version_id_fkey;
ALTER TABLE public.scrna_datasets DROP COLUMN IF EXISTS genome_version_id;

COMMIT;
