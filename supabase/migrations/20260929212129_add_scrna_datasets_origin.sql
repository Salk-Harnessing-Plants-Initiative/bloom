-- 20260929212129_add_scrna_datasets_origin.sql
--
-- Adds scrna_datasets.origin: whether a dataset is HPI's own ('hpi') or a public one
-- ('public', with its source in url). The scRNA job form asks for it, and a finished
-- run's results are loaded with it. Reference atlases (kind 'reference') are published
-- atlases, so they are set to 'public'; other datasets stay NULL until someone records
-- where they came from.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

ALTER TABLE public.scrna_datasets ADD COLUMN IF NOT EXISTS origin TEXT;

ALTER TABLE public.scrna_datasets DROP CONSTRAINT IF EXISTS scrna_datasets_origin_check;
ALTER TABLE public.scrna_datasets ADD CONSTRAINT scrna_datasets_origin_check
    CHECK (origin IN ('hpi', 'public'));

COMMENT ON COLUMN public.scrna_datasets.origin IS
    'hpi: HPI''s own data. public: a published dataset, whose source is in url. '
    'NULL: not recorded.';

UPDATE public.scrna_datasets SET origin = 'public'
WHERE kind = 'reference' AND origin IS NULL;

COMMIT;
