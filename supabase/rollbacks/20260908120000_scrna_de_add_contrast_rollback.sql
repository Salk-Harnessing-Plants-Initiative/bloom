-- Manual rollback for 20260908120000_scrna_de_add_contrast.sql
--
-- Drops the contrast dimension from scrna_de and restores file_path to NOT
-- NULL. One-vs-rest rows -- everything that predates the migration -- come
-- through unchanged, because their values in these columns are all NULL.
--
-- Refuses to run if any row holds data this would destroy. Dropping the ten
-- columns discards every contrast, group name and count as surely as a DELETE
-- would, and nothing automated runs these scripts: the only time one runs is
-- by hand, against a table someone has already filled.
--
-- Convention for this repo: a rollback that drops a table, drops a column or
-- deletes rows counts what it would destroy first and raises if that count is
-- not zero. 20260722000200_create_cyl_intermediates_bucket_rollback.sql set the
-- pattern; this follows it.
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

DO $$
DECLARE at_risk bigint;
BEGIN
  SELECT count(*) INTO at_risk
    FROM public.scrna_de
   WHERE file_path IS NULL
      OR num_nonnulls(contrast, group1, group2, n_group1, n_group2,
                      n_genes_tested, n_significant_fdr,
                      n_significant_fdr_lfc, n_up, n_down) > 0;
  IF at_risk > 0 THEN
    RAISE EXCEPTION
      'Refusing to roll back: % row(s) in scrna_de hold contrast data this '
      'would destroy. Export or remove them deliberately, then re-run.', at_risk;
  END IF;
END $$;

DROP POLICY IF EXISTS user_read_scrna_de  ON public.scrna_de;
DROP POLICY IF EXISTS agent_read_scrna_de ON public.scrna_de;
DROP POLICY IF EXISTS admin_all_scrna_de  ON public.scrna_de;

-- The uniqueness index goes with its constraint below; only the older index the
-- migration dropped needs recreating here.
CREATE INDEX IF NOT EXISTS idx_scrna_de_dataset_cluster
  ON public.scrna_de (dataset_id, cluster_id);

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_comparison_uniqueness,
  DROP CONSTRAINT IF EXISTS scrna_de_contrast_rows_carry_counts,
  DROP CONSTRAINT IF EXISTS scrna_de_name_lengths,
  DROP CONSTRAINT IF EXISTS scrna_de_text_not_blank,
  DROP CONSTRAINT IF EXISTS scrna_de_counts_non_negative,
  DROP CONSTRAINT IF EXISTS scrna_de_up_plus_down_is_lfc_significant,
  DROP CONSTRAINT IF EXISTS scrna_de_lfc_cut_narrows_fdr_cut,
  DROP CONSTRAINT IF EXISTS scrna_de_significant_within_tested,
  DROP CONSTRAINT IF EXISTS scrna_de_counts_all_or_none,
  DROP CONSTRAINT IF EXISTS scrna_de_no_file_means_nothing_tested,
  DROP CONSTRAINT IF EXISTS scrna_de_groups_differ,
  DROP CONSTRAINT IF EXISTS scrna_de_contrast_names_both_groups;

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
