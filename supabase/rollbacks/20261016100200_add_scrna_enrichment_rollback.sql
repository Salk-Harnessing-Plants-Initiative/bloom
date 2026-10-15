-- Rollback for 20261016100200_add_scrna_enrichment.sql
-- Manual break-glass only; nothing runs it automatically. Drops the enrichment tables (which
-- erases every enrichment result) and scrna_datasets.genome_version_id (which erases which
-- genome version each dataset was mapped to). Run the rollback of 20261016100300 first.

BEGIN;

DROP TRIGGER IF EXISTS rnaseq_runs_link_dataset_genome ON public.rnaseq_runs;
DROP FUNCTION IF EXISTS public.rnaseq_runs_link_dataset_genome();

DROP TABLE IF EXISTS public.scrna_enrichment_terms;
DROP TABLE IF EXISTS public.scrna_enrichment_runs;
DROP FUNCTION IF EXISTS public.scrna_enrichment_terms_guard();
DROP FUNCTION IF EXISTS public.scrna_enrichment_runs_guard();

DROP TRIGGER IF EXISTS scrna_datasets_keep_genome_version ON public.scrna_datasets;
DROP FUNCTION IF EXISTS public.scrna_datasets_keep_genome_version();
DROP INDEX IF EXISTS public.scrna_datasets_genome_version_id_idx;
ALTER TABLE public.scrna_datasets DROP CONSTRAINT IF EXISTS scrna_datasets_genome_version_id_fkey;
ALTER TABLE public.scrna_datasets DROP COLUMN IF EXISTS genome_version_id;

COMMIT;
