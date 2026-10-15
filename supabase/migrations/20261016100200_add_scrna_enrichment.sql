-- 20261016100200_add_scrna_enrichment.sql
--
-- Which genome version a single-cell dataset was mapped to, and the GO and pathway terms
-- enriched in its differential expression. A dataset's genome version is set once: by the
-- Cell Ranger run that loaded it, or by a writer for an older dataset (20261016100300). An
-- enrichment run tests one differential expression analysis of a dataset against one ready
-- annotation build of that dataset's genome version, with the settings it records, and keeps
-- the significant terms per comparison and direction. A run is uploading, then ready or
-- abandoned; a ready run never changes.
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

-- 2. Enrichment tables ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.scrna_enrichment_runs (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY,
    dataset_id BIGINT NOT NULL,
    -- The differential expression analysis whose comparisons were tested.
    de_run_id BIGINT NOT NULL,
    annotation_build_id BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploading',
    -- e.g. {"top_n": 300, "fdr_max": 0.05, "lfc_min": 0.25, "min_term": 5, "max_term": 500,
    --       "kept_fdr_max": 0.05}
    params JSONB NOT NULL,
    term_count INTEGER,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ready_at TIMESTAMPTZ
);

-- One row per significant term of one comparison and direction.
CREATE TABLE IF NOT EXISTS public.scrna_enrichment_terms (
    run_id BIGINT NOT NULL,
    dataset_id BIGINT NOT NULL,
    de_id BIGINT NOT NULL,
    direction TEXT NOT NULL,
    kind TEXT NOT NULL,
    term_id TEXT NOT NULL,
    -- List genes in the term, the list, the term's annotated genes and all annotated genes.
    hit_count INTEGER NOT NULL,
    list_size INTEGER NOT NULL,
    term_size INTEGER NOT NULL,
    background_size INTEGER NOT NULL,
    pvalue DOUBLE PRECISION NOT NULL,
    fdr DOUBLE PRECISION NOT NULL,
    -- The list genes in the term, as scrna_genes ids.
    gene_ids BIGINT[] NOT NULL
);

-- 3. Keys ----------------------------------------------------------------------------------

DO $$
DECLARE
  v_key RECORD;
  existing TEXT;
BEGIN
  FOR v_key IN
    SELECT * FROM (VALUES
      ('scrna_enrichment_runs', 'scrna_enrichment_runs_pkey', 'PRIMARY KEY (id)'),
      ('scrna_enrichment_runs', 'scrna_enrichment_runs_id_per_dataset', 'UNIQUE (dataset_id, id)'),
      ('scrna_enrichment_terms', 'scrna_enrichment_terms_pkey',
       'PRIMARY KEY (run_id, de_id, direction, kind, term_id)')
    ) AS k(tbl, name, def)
  LOOP
    SELECT pg_get_constraintdef(c.oid) INTO existing
      FROM pg_constraint c
     WHERE c.conname = v_key.name AND c.conrelid = format('public.%I', v_key.tbl)::regclass;
    IF existing IS NULL THEN
      EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I %s', v_key.tbl, v_key.name, v_key.def);
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
      ('scrna_enrichment_runs', 'scrna_enrichment_runs_dataset_id_fkey',
       'FOREIGN KEY (dataset_id) REFERENCES public.scrna_datasets(id)'),
      ('scrna_enrichment_runs', 'scrna_enrichment_runs_de_run_fkey',
       'FOREIGN KEY (dataset_id, de_run_id) REFERENCES public.scrna_de_runs(dataset_id, id)'),
      ('scrna_enrichment_runs', 'scrna_enrichment_runs_annotation_build_id_fkey',
       'FOREIGN KEY (annotation_build_id) REFERENCES public.genome_annotation_builds(id)'),
      ('scrna_enrichment_terms', 'scrna_enrichment_terms_run_fkey',
       'FOREIGN KEY (dataset_id, run_id) REFERENCES public.scrna_enrichment_runs(dataset_id, id)'),
      ('scrna_enrichment_terms', 'scrna_enrichment_terms_comparison_fkey',
       'FOREIGN KEY (dataset_id, de_id) REFERENCES public.scrna_de(dataset_id, id)'),
      ('scrna_enrichment_terms', 'scrna_enrichment_terms_term_fkey',
       'FOREIGN KEY (kind, term_id) REFERENCES public.functional_terms(kind, term_id)')
    ) AS k(tbl, name, def)
  LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint c
       WHERE c.conname = v_fk.name AND c.conrelid = format('public.%I', v_fk.tbl)::regclass
    ) THEN
      EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I %s', v_fk.tbl, v_fk.name, v_fk.def);
    END IF;
  END LOOP;
END $$;

-- The same analysis, annotation and settings are run once while live.
CREATE UNIQUE INDEX IF NOT EXISTS scrna_enrichment_runs_one_live
    ON public.scrna_enrichment_runs (dataset_id, de_run_id, annotation_build_id, params)
    WHERE status IN ('uploading', 'ready');

-- The view reads one comparison's terms.
CREATE INDEX IF NOT EXISTS scrna_enrichment_terms_comparison_idx
    ON public.scrna_enrichment_terms (run_id, de_id);

-- 4. Checks --------------------------------------------------------------------------------

ALTER TABLE public.scrna_enrichment_runs
  DROP CONSTRAINT IF EXISTS scrna_enrichment_runs_status_check,
  ADD CONSTRAINT scrna_enrichment_runs_status_check
    CHECK (status IN ('uploading', 'ready', 'abandoned')),
  DROP CONSTRAINT IF EXISTS scrna_enrichment_runs_params_check,
  ADD CONSTRAINT scrna_enrichment_runs_params_check CHECK (
    jsonb_typeof(params) = 'object' AND length(params::text) <= 2000
  ),
  -- A ready run has its term count (zero when nothing was significant) and ready_at.
  DROP CONSTRAINT IF EXISTS scrna_enrichment_runs_finished_check,
  ADD CONSTRAINT scrna_enrichment_runs_finished_check CHECK (
    CASE WHEN status = 'ready' THEN num_nulls(term_count, ready_at) = 0 AND term_count >= 0
    ELSE num_nonnulls(term_count, ready_at) = 0 END
  );

ALTER TABLE public.scrna_enrichment_terms
  DROP CONSTRAINT IF EXISTS scrna_enrichment_terms_direction_check,
  ADD CONSTRAINT scrna_enrichment_terms_direction_check CHECK (direction IN ('up', 'down')),
  -- Counts nest: hits are in both the list and the term, and both are in the background.
  DROP CONSTRAINT IF EXISTS scrna_enrichment_terms_counts_check,
  ADD CONSTRAINT scrna_enrichment_terms_counts_check CHECK (
    hit_count >= 1 AND hit_count <= list_size AND hit_count <= term_size
    AND list_size <= background_size AND term_size <= background_size
    AND cardinality(gene_ids) = hit_count
  ),
  -- Correction only ever raises a p-value.
  DROP CONSTRAINT IF EXISTS scrna_enrichment_terms_probabilities_check,
  ADD CONSTRAINT scrna_enrichment_terms_probabilities_check CHECK (
    pvalue >= 0 AND pvalue <= 1 AND fdr >= pvalue AND fdr <= 1
  );

-- 5. What may change -----------------------------------------------------------------------

-- A run starts as uploading; its identity never changes; it moves only to ready or abandoned,
-- once; a ready run cannot be deleted.
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
       OR NEW.params IS DISTINCT FROM OLD.params OR NEW.created_by IS DISTINCT FROM OLD.created_by
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

-- A run's terms are added or removed only while it is uploading.
CREATE OR REPLACE FUNCTION public.scrna_enrichment_terms_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_run_id BIGINT := CASE WHEN TG_OP = 'INSERT' THEN NEW.run_id ELSE OLD.run_id END;
    v_status TEXT;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.run_id IS DISTINCT FROM OLD.run_id THEN
        RAISE EXCEPTION 'a term cannot move to another enrichment run' USING ERRCODE = '55000';
    END IF;
    SELECT r.status INTO v_status FROM public.scrna_enrichment_runs r WHERE r.id = v_run_id;
    IF v_status IS DISTINCT FROM 'uploading' THEN
        RAISE EXCEPTION 'enrichment run % is %; its terms cannot change', v_run_id,
            coalesce(v_status, 'missing') USING ERRCODE = '55000';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

DROP TRIGGER IF EXISTS scrna_enrichment_terms_guard ON public.scrna_enrichment_terms;
CREATE TRIGGER scrna_enrichment_terms_guard
    BEFORE INSERT OR UPDATE OR DELETE ON public.scrna_enrichment_terms
    FOR EACH ROW EXECUTE FUNCTION public.scrna_enrichment_terms_guard();

-- 6. Access --------------------------------------------------------------------------------

DO $$
DECLARE
  v_table TEXT;
BEGIN
  FOREACH v_table IN ARRAY ARRAY['scrna_enrichment_runs', 'scrna_enrichment_terms'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', v_table);
    EXECUTE format('DROP POLICY IF EXISTS admin_all_%1$s ON public.%1$I', v_table);
    EXECUTE format(
      'CREATE POLICY admin_all_%1$s ON public.%1$I FOR ALL TO bloom_admin USING (true) WITH CHECK (true)',
      v_table
    );
    EXECUTE format('DROP POLICY IF EXISTS user_read_%1$s ON public.%1$I', v_table);
    EXECUTE format(
      'CREATE POLICY user_read_%1$s ON public.%1$I FOR SELECT TO bloom_user USING (true)', v_table
    );
    EXECUTE format('DROP POLICY IF EXISTS agent_read_%1$s ON public.%1$I', v_table);
    EXECUTE format(
      'CREATE POLICY agent_read_%1$s ON public.%1$I FOR SELECT TO bloom_agent USING (true)', v_table
    );
    EXECUTE format('DROP POLICY IF EXISTS workflows_read_%1$s ON public.%1$I', v_table);
    EXECUTE format(
      'CREATE POLICY workflows_read_%1$s ON public.%1$I FOR SELECT TO bloom_workflows USING (true)',
      v_table
    );
    EXECUTE format(
      'REVOKE ALL ON public.%I FROM anon, authenticated, bloom_user, bloom_agent, bloom_writer, bloom_workflows',
      v_table
    );
    EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON public.%I FROM service_role', v_table);
    EXECUTE format('GRANT SELECT ON public.%I TO bloom_user, bloom_agent, bloom_workflows', v_table);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO bloom_admin', v_table);
  END LOOP;
END
$$;

REVOKE EXECUTE ON FUNCTION public.scrna_datasets_keep_genome_version() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.rnaseq_runs_link_dataset_genome() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.scrna_enrichment_runs_guard() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.scrna_enrichment_terms_guard() FROM PUBLIC, anon, authenticated;

COMMIT;
