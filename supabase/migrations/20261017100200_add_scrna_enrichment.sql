-- 20261017100200_add_scrna_enrichment.sql
--
-- Which genome version a single-cell dataset was mapped to, and the GO and pathway enrichment
-- of its differential expression. A dataset's genome version is set once: by the Cell Ranger
-- run that loaded it, or by a writer for an older dataset (20261017100300). An enrichment run
-- tests one differential expression analysis of a dataset against one ready annotation build
-- of that dataset's genome version, with the settings it records. Its significant terms live in
-- one gzipped JSON file in the scrna bucket, at enrichment/<dataset>/run-<id>.json.gz; this
-- row records the file's checksum and size, how many terms it holds, and how many background
-- genes the annotation covered, so a gene ID mismatch is not read as no enrichment. A run is
-- uploading, then ready or abandoned; a ready run never changes.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

SET LOCAL lock_timeout = '5s';

-- 1. A dataset's genome version ------------------------------------------------------------

ALTER TABLE public.scrna_datasets ADD COLUMN IF NOT EXISTS genome_version_id BIGINT;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conname = 'scrna_datasets_genome_version_id_fkey'
       AND conrelid = 'public.scrna_datasets'::regclass
  ) THEN
    ALTER TABLE public.scrna_datasets ADD CONSTRAINT scrna_datasets_genome_version_id_fkey
      FOREIGN KEY (genome_version_id) REFERENCES public.genome_reference_versions(id);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS scrna_datasets_genome_version_id_idx
    ON public.scrna_datasets (genome_version_id);

-- A Cell Ranger run linked to the dataset it loaded gives the dataset its genome version, in
-- the same call that links them (link_rnaseq_run_dataset, otherwise unchanged from
-- 20261007000000).
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
  v_genome BIGINT;
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

  -- The run's genome version becomes the dataset's when it is ready and of the dataset's
  -- species; otherwise the dataset is left for a writer to link.
  SELECT v.id INTO v_genome
    FROM public.genome_reference_versions v
    JOIN public.genome_references g ON g.id = v.genome_id
   WHERE v.id = v_run.genome_version_id AND v.status = 'ready'
     AND g.species_id = v_dataset.species_id;

  UPDATE public.scrna_datasets
     SET url = coalesce(nullif(btrim(v_run.metadata ->> 'source_url'), ''), url),
         genome_version_id = coalesce(genome_version_id, v_genome),
         metadata = coalesce(metadata, '{}'::JSONB) || jsonb_build_object('source', v_source),
         created_by = v_run.requested_by
   WHERE id = v_dataset.id;

  UPDATE public.rnaseq_runs SET dataset_id = v_dataset.id WHERE id = p_run_id;

  RETURN v_dataset.id;
END;
$$;

-- Datasets already loaded by a run on a ready genome version of their species take it now;
-- any other is left for a writer to link.
UPDATE public.scrna_datasets d
   SET genome_version_id = r.genome_version_id
  FROM public.rnaseq_runs r
  JOIN public.genome_reference_versions v ON v.id = r.genome_version_id AND v.status = 'ready'
  JOIN public.genome_references g ON g.id = v.genome_id
 WHERE r.dataset_id = d.id AND d.genome_version_id IS NULL AND g.species_id = d.species_id;

-- 2. Enrichment runs -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.scrna_enrichment_runs (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY,
    dataset_id BIGINT NOT NULL,
    -- The differential expression analysis whose comparisons were tested.
    de_run_id BIGINT NOT NULL,
    annotation_build_id BIGINT NOT NULL,
    -- The pipeline job that produced the run; empty for one uploaded by hand.
    rnaseq_run_id BIGINT,
    status TEXT NOT NULL DEFAULT 'uploading',
    -- e.g. {"top_n": 300, "fdr_max": 0.05, "lfc_min": 0.25, "min_term": 5, "max_term": 500,
    --       "kept_fdr_max": 0.05}
    params JSONB NOT NULL,
    -- The object in the scrna bucket, set from the dataset and the run.
    file_path TEXT NOT NULL,
    -- Set when the run is finished.
    file_sha256 TEXT,
    file_bytes BIGINT,
    term_count INTEGER,
    -- Genes tested against, and how many of them have a term in the annotation.
    background_genes INTEGER,
    annotated_genes INTEGER,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ready_at TIMESTAMPTZ
);

-- 3. Keys ----------------------------------------------------------------------------------

DO $$
DECLARE
  v_key RECORD;
  existing TEXT;
BEGIN
  FOR v_key IN
    SELECT * FROM (VALUES
      ('scrna_enrichment_runs_pkey', 'PRIMARY KEY (id)'),
      ('scrna_enrichment_runs_file_path_key', 'UNIQUE (file_path)')
    ) AS k(name, def)
  LOOP
    SELECT pg_get_constraintdef(c.oid) INTO existing
      FROM pg_constraint c
     WHERE c.conname = v_key.name AND c.conrelid = 'public.scrna_enrichment_runs'::regclass;
    IF existing IS NULL THEN
      EXECUTE format('ALTER TABLE public.scrna_enrichment_runs ADD CONSTRAINT %I %s',
                     v_key.name, v_key.def);
    ELSIF existing <> v_key.def THEN
      RAISE EXCEPTION '% is %, expected %', v_key.name, existing, v_key.def;
    END IF;
  END LOOP;
END $$;

DO $$
DECLARE
  v_fk RECORD;
BEGIN
  FOR v_fk IN
    SELECT * FROM (VALUES
      ('scrna_enrichment_runs_dataset_id_fkey',
       'FOREIGN KEY (dataset_id) REFERENCES public.scrna_datasets(id)'),
      ('scrna_enrichment_runs_de_run_fkey',
       'FOREIGN KEY (dataset_id, de_run_id) REFERENCES public.scrna_de_runs(dataset_id, id)'),
      ('scrna_enrichment_runs_annotation_build_id_fkey',
       'FOREIGN KEY (annotation_build_id) REFERENCES public.genome_annotation_builds(id)'),
      ('scrna_enrichment_runs_rnaseq_run_id_fkey',
       'FOREIGN KEY (rnaseq_run_id) REFERENCES public.rnaseq_runs(id)')
    ) AS k(name, def)
  LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint c
       WHERE c.conname = v_fk.name AND c.conrelid = 'public.scrna_enrichment_runs'::regclass
    ) THEN
      EXECUTE format('ALTER TABLE public.scrna_enrichment_runs ADD CONSTRAINT %I %s',
                     v_fk.name, v_fk.def);
    END IF;
  END LOOP;
END $$;

-- The same analysis, annotation and settings are run once while live.
CREATE UNIQUE INDEX IF NOT EXISTS scrna_enrichment_runs_one_live
    ON public.scrna_enrichment_runs (dataset_id, de_run_id, annotation_build_id, params)
    WHERE status IN ('uploading', 'ready');

-- A dataset's view and a pipeline job's page list their runs.
CREATE INDEX IF NOT EXISTS scrna_enrichment_runs_dataset_id_idx
    ON public.scrna_enrichment_runs (dataset_id);
CREATE INDEX IF NOT EXISTS scrna_enrichment_runs_rnaseq_run_id_idx
    ON public.scrna_enrichment_runs (rnaseq_run_id);

-- 4. Checks --------------------------------------------------------------------------------

ALTER TABLE public.scrna_enrichment_runs
  DROP CONSTRAINT IF EXISTS scrna_enrichment_runs_status_check,
  ADD CONSTRAINT scrna_enrichment_runs_status_check
    CHECK (status IN ('uploading', 'ready', 'abandoned')),
  DROP CONSTRAINT IF EXISTS scrna_enrichment_runs_params_check,
  ADD CONSTRAINT scrna_enrichment_runs_params_check CHECK (
    jsonb_typeof(params) = 'object' AND length(params::text) <= 2000
  ),
  DROP CONSTRAINT IF EXISTS scrna_enrichment_runs_file_sha256_check,
  ADD CONSTRAINT scrna_enrichment_runs_file_sha256_check CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
  -- A ready run has its file's checksum and size, its term count (zero when nothing was
  -- significant), its gene counts and ready_at; any other run has none of them.
  DROP CONSTRAINT IF EXISTS scrna_enrichment_runs_finished_check,
  ADD CONSTRAINT scrna_enrichment_runs_finished_check CHECK (
    CASE WHEN status = 'ready' THEN
      num_nulls(file_sha256, file_bytes, term_count, background_genes, annotated_genes,
                ready_at) = 0
      AND file_bytes > 0 AND term_count >= 0
      AND background_genes > 0 AND annotated_genes BETWEEN 0 AND background_genes
    ELSE
      num_nonnulls(file_sha256, file_bytes, term_count, background_genes, annotated_genes,
                   ready_at) = 0
    END
  );

-- 5. What may change -----------------------------------------------------------------------

-- A run starts as uploading and gets its file path; its identity never changes; it moves only
-- to ready or abandoned, once; a ready run cannot be deleted.
CREATE OR REPLACE FUNCTION public.scrna_enrichment_runs_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'uploading' THEN
            RAISE EXCEPTION 'an enrichment run starts as uploading' USING ERRCODE = '55000';
        END IF;
        NEW.file_path := 'enrichment/' || NEW.dataset_id || '/run-' || NEW.id || '.json.gz';
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'ready' THEN
            RAISE EXCEPTION 'enrichment run % is ready; it cannot be deleted', OLD.id
                USING ERRCODE = '55000';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
       OR NEW.de_run_id IS DISTINCT FROM OLD.de_run_id
       OR NEW.annotation_build_id IS DISTINCT FROM OLD.annotation_build_id
       OR NEW.rnaseq_run_id IS DISTINCT FROM OLD.rnaseq_run_id
       OR NEW.params IS DISTINCT FROM OLD.params OR NEW.file_path IS DISTINCT FROM OLD.file_path
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'an enrichment run''s identity cannot change' USING ERRCODE = '55000';
    END IF;
    IF OLD.status <> 'uploading' THEN
        RAISE EXCEPTION 'enrichment run % is %; it cannot change', OLD.id, OLD.status
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS scrna_enrichment_runs_guard ON public.scrna_enrichment_runs;
CREATE TRIGGER scrna_enrichment_runs_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.scrna_enrichment_runs
    FOR EACH ROW EXECUTE FUNCTION public.scrna_enrichment_runs_guard();

-- 6. Access --------------------------------------------------------------------------------

ALTER TABLE public.scrna_enrichment_runs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS admin_all_scrna_enrichment_runs ON public.scrna_enrichment_runs;
CREATE POLICY admin_all_scrna_enrichment_runs ON public.scrna_enrichment_runs
    FOR ALL TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS user_read_scrna_enrichment_runs ON public.scrna_enrichment_runs;
CREATE POLICY user_read_scrna_enrichment_runs ON public.scrna_enrichment_runs
    FOR SELECT TO bloom_user USING (true);
DROP POLICY IF EXISTS agent_read_scrna_enrichment_runs ON public.scrna_enrichment_runs;
CREATE POLICY agent_read_scrna_enrichment_runs ON public.scrna_enrichment_runs
    FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS workflows_read_scrna_enrichment_runs ON public.scrna_enrichment_runs;
CREATE POLICY workflows_read_scrna_enrichment_runs ON public.scrna_enrichment_runs
    FOR SELECT TO bloom_workflows USING (true);

REVOKE ALL ON public.scrna_enrichment_runs
    FROM anon, authenticated, bloom_user, bloom_agent, bloom_writer, bloom_workflows;
REVOKE UPDATE, DELETE, TRUNCATE ON public.scrna_enrichment_runs FROM service_role;
GRANT SELECT ON public.scrna_enrichment_runs TO bloom_user, bloom_agent, bloom_workflows;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.scrna_enrichment_runs TO bloom_admin;

REVOKE EXECUTE ON FUNCTION public.scrna_enrichment_runs_guard() FROM PUBLIC, anon, authenticated;

COMMIT;
