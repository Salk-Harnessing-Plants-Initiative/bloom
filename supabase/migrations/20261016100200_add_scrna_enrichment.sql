-- 20261016100200_add_scrna_enrichment.sql
--
-- Which genome version a single-cell dataset was mapped to, and the GO and pathway enrichment
-- of its differential expression. A dataset's genome version is set once: by the Cell Ranger
-- run that loaded it, or by a writer for an older dataset (20261016100300). An enrichment run
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

-- A dataset's genome version is a ready version of the dataset's species, and once set it
-- never changes or clears.
CREATE OR REPLACE FUNCTION public.scrna_datasets_keep_genome_version()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_status TEXT;
    v_species BIGINT;
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.genome_version_id IS NOT NULL THEN
        IF NEW.genome_version_id IS DISTINCT FROM OLD.genome_version_id THEN
            RAISE EXCEPTION 'dataset % is already linked to genome version %; it cannot change',
                OLD.id, OLD.genome_version_id USING ERRCODE = '55000';
        END IF;
        IF NEW.species_id IS DISTINCT FROM OLD.species_id THEN
            RAISE EXCEPTION 'dataset % is linked to genome version %; its species cannot change',
                OLD.id, OLD.genome_version_id USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.genome_version_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT v.status, g.species_id INTO v_status, v_species
      FROM public.genome_reference_versions v
      JOIN public.genome_references g ON g.id = v.genome_id
     WHERE v.id = NEW.genome_version_id;
    IF v_status IS DISTINCT FROM 'ready' THEN
        RAISE EXCEPTION 'genome version % is not ready', NEW.genome_version_id
            USING ERRCODE = '22023';
    END IF;
    IF v_species IS DISTINCT FROM NEW.species_id THEN
        RAISE EXCEPTION 'genome version % is for species %, not the dataset''s species %',
            NEW.genome_version_id, v_species, NEW.species_id USING ERRCODE = '22023';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS scrna_datasets_keep_genome_version ON public.scrna_datasets;
CREATE TRIGGER scrna_datasets_keep_genome_version
    BEFORE INSERT OR UPDATE OF genome_version_id, species_id ON public.scrna_datasets
    FOR EACH ROW EXECUTE FUNCTION public.scrna_datasets_keep_genome_version();

-- A Cell Ranger run linked to the dataset it loaded gives the dataset its genome version. The
-- dataset trigger refuses a version of another species; the run's link is then left unmade
-- rather than failing the load, and a writer can look into it.
CREATE OR REPLACE FUNCTION public.rnaseq_runs_link_dataset_genome()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.dataset_id IS NOT NULL AND NEW.genome_version_id IS NOT NULL THEN
        BEGIN
            UPDATE public.scrna_datasets
               SET genome_version_id = NEW.genome_version_id
             WHERE id = NEW.dataset_id AND genome_version_id IS NULL;
        EXCEPTION WHEN invalid_parameter_value THEN
            RAISE WARNING 'dataset % not linked to genome version %: %', NEW.dataset_id,
                NEW.genome_version_id, SQLERRM;
        END;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS rnaseq_runs_link_dataset_genome ON public.rnaseq_runs;
CREATE TRIGGER rnaseq_runs_link_dataset_genome
    AFTER INSERT OR UPDATE OF dataset_id ON public.rnaseq_runs
    FOR EACH ROW EXECUTE FUNCTION public.rnaseq_runs_link_dataset_genome();

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

REVOKE EXECUTE ON FUNCTION public.scrna_datasets_keep_genome_version() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.rnaseq_runs_link_dataset_genome() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.scrna_enrichment_runs_guard() FROM PUBLIC, anon, authenticated;

COMMIT;
