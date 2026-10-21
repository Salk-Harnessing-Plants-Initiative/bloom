-- 20261020120000_add_pipeline_go_pathways_steps.sql
--
-- The Cell Ranger pipeline makes each new dataset's GO and pathway results: a run may report
-- `annotate-genome` (its genome version's annotation, fetched or built) and `go-pathways` (the
-- dataset's enrichment). Each species may record its KEGG organism code (e.g. sbi), which the
-- annotation uses for the species' pathways; a species without one gets GO only.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

-- 1. The steps a run reports ---------------------------------------------------------------

ALTER TABLE public.rnaseq_runs DROP CONSTRAINT IF EXISTS rnaseq_runs_current_step_check;
ALTER TABLE public.rnaseq_runs ADD CONSTRAINT rnaseq_runs_current_step_check CHECK (
    current_step IS NULL
    OR (workflow_type = 'scrna-cellranger'
        AND current_step IN ('fetch-sra', 'stage-reference', 'download-genome', 'build-reference',
                             'stage', 'qc', 'count', 'preprocess', 'cluster', 'de', 'build-h5ad',
                             'load-dataset', 'annotate-genome', 'go-pathways', 'cleanup'))
);

-- 2. A species' KEGG organism code -----------------------------------------------------------

ALTER TABLE public.species ADD COLUMN IF NOT EXISTS kegg_org TEXT;
ALTER TABLE public.species DROP CONSTRAINT IF EXISTS species_kegg_org_check;
ALTER TABLE public.species ADD CONSTRAINT species_kegg_org_check
    CHECK (kegg_org IS NULL OR kegg_org ~ '^[a-z]{3,4}$');
COMMENT ON COLUMN public.species.kegg_org IS
    'KEGG organism code (e.g. sbi) for the species'' pathways in GO and pathway annotation; NULL: GO only';

COMMIT;
