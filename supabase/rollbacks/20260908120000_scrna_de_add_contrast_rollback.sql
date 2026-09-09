-- Manual rollback for 20260908120000_scrna_de_add_contrast.sql
--
-- Drops the contrast dimension from scrna_de and restores file_path to NOT
-- NULL. One-vs-rest rows -- everything that predates the migration -- come
-- through unchanged, because their values in these columns are all NULL.
--
-- Data loss, in two ways:
--   * the contrast, group labels, cell counts and summary counts are dropped
--   * rows recording a comparison that never ran are deleted outright, since
--     they exist only as a NULL file_path and NOT NULL cannot be restored
--     while they are present
--
-- The bloom_admin / bloom_agent / bloom_user policies are also removed. They
-- close a gap that predates this migration -- scrna_de was the one scrna_*
-- table 20260506000001 missed -- so dropping them returns bloom_user DE reads
-- to returning no rows, as before.
--
-- No grants are revoked. The migration adds none: those predate it, from the
-- ALL TABLES grant in 20260414002000. Revoking them here would strip access
-- the table has had since April and nothing would put it back.

BEGIN;

DROP POLICY IF EXISTS user_read_scrna_de  ON public.scrna_de;
DROP POLICY IF EXISTS agent_read_scrna_de ON public.scrna_de;
DROP POLICY IF EXISTS admin_all_scrna_de  ON public.scrna_de;

DROP INDEX IF EXISTS public.idx_scrna_de_dataset_cluster_contrast;

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_counts_non_negative,
  DROP CONSTRAINT IF EXISTS scrna_de_no_file_means_nothing_tested,
  DROP CONSTRAINT IF EXISTS scrna_de_groups_differ,
  DROP CONSTRAINT IF EXISTS scrna_de_contrast_names_both_groups;

-- Comparisons that never ran have no file to point at.
DELETE FROM public.scrna_de WHERE file_path IS NULL;

ALTER TABLE public.scrna_de
  ALTER COLUMN file_path SET NOT NULL;

ALTER TABLE public.scrna_de
  DROP COLUMN IF EXISTS n_down,
  DROP COLUMN IF EXISTS n_up,
  DROP COLUMN IF EXISTS n_significant_fdr_lfc,
  DROP COLUMN IF EXISTS n_significant_fdr,
  DROP COLUMN IF EXISTS n_genes_tested,
  DROP COLUMN IF EXISTS n_group2,
  DROP COLUMN IF EXISTS n_group1,
  DROP COLUMN IF EXISTS group2,
  DROP COLUMN IF EXISTS group1,
  DROP COLUMN IF EXISTS contrast;

COMMIT;
