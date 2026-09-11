-- Differential expression results are gene rows under an analysis now. The five
-- summary counts go, a new result has to belong to an analysis, and a few gaps
-- left by 20260911000000 are closed.

BEGIN;

-- 1. The summary counts --------------------------------------------------------
-- Each was an answer at one FDR and fold-change cut, and nothing recorded which.
-- The gene rows answer the same question at whatever cut a reader asks with.

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_counts_all_or_none,
  DROP CONSTRAINT IF EXISTS scrna_de_counts_non_negative,
  DROP CONSTRAINT IF EXISTS scrna_de_significant_within_tested,
  DROP CONSTRAINT IF EXISTS scrna_de_lfc_cut_narrows_fdr_cut,
  DROP CONSTRAINT IF EXISTS scrna_de_up_plus_down_is_lfc_significant,
  DROP CONSTRAINT IF EXISTS scrna_de_contrast_rows_carry_counts,
  DROP CONSTRAINT IF EXISTS scrna_de_untested_counted_nothing;

ALTER TABLE public.scrna_de
  DROP COLUMN IF EXISTS n_genes_tested,
  DROP COLUMN IF EXISTS n_significant_fdr,
  DROP COLUMN IF EXISTS n_significant_fdr_lfc,
  DROP COLUMN IF EXISTS n_up,
  DROP COLUMN IF EXISTS n_down;

-- counts_non_negative also covered the group sizes; they keep that rule.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_group_sizes_non_negative;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_group_sizes_non_negative
  CHECK (n_group1 >= 0 AND n_group2 >= 0);


-- 2. A result belongs to an analysis --------------------------------------------
-- Rows that predate runs keep their file and carry none of the analysis columns.
-- Everything written from now on belongs to a run and keeps its genes as rows.

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_run_metadata_needs_a_run,
  DROP CONSTRAINT IF EXISTS scrna_de_run_rows_name_no_file,
  DROP CONSTRAINT IF EXISTS scrna_de_new_contrasts_belong_to_a_run;

ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_run_metadata_needs_a_run
  CHECK (run_id IS NOT NULL
         OR num_nonnulls(group_kind, method, params_hash, tested) = 0);

ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_run_rows_name_no_file
  CHECK (run_id IS NULL OR file_path IS NULL);

-- NOT VALID: contrast rows loaded before runs existed stay as they are.
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_new_contrasts_belong_to_a_run
  CHECK (run_id IS NOT NULL OR contrast IS NULL) NOT VALID;


-- 3. A cell type stays in its dataset -------------------------------------------
-- scrna_de and scrna_cells follow a cell type's key by ON UPDATE CASCADE, and the
-- key includes the dataset. Renaming the identifier should cascade; moving the
-- cell type, and the results with it, to another dataset should not happen.

CREATE OR REPLACE FUNCTION public.scrna_clusters_keep_their_dataset()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
  RAISE EXCEPTION 'cell type % belongs to dataset % and cannot move to another',
    OLD.cluster_id, OLD.dataset_id
    USING ERRCODE = 'check_violation';
END
$$;

DROP TRIGGER IF EXISTS scrna_clusters_keep_their_dataset ON public.scrna_clusters;
CREATE TRIGGER scrna_clusters_keep_their_dataset
  BEFORE UPDATE OF dataset_id ON public.scrna_clusters
  FOR EACH ROW
  WHEN (NEW.dataset_id IS DISTINCT FROM OLD.dataset_id)
  EXECUTE FUNCTION public.scrna_clusters_keep_their_dataset();


-- 4. Closing what 20260911000000 left open --------------------------------------

-- Staging and production hold no result naming a missing cell type.
ALTER TABLE public.scrna_de VALIDATE CONSTRAINT scrna_de_cluster_in_catalogue;

-- Stated here rather than inherited from default privileges, which depend on
-- the role that runs the migration.
GRANT SELECT ON public.scrna_de_runs, public.scrna_de_genes TO bloom_user, bloom_agent;
GRANT SELECT, INSERT ON public.scrna_de_runs, public.scrna_de_genes TO bloom_writer;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON public.scrna_de_runs, public.scrna_de_genes TO bloom_admin;

COMMENT ON CONSTRAINT scrna_de_genes_fold_change_is_a_number_or_nothing
  ON public.scrna_de_genes IS
  'NaN is refused; a fold change that could not be computed is NULL. Postgres '
  'ranks NaN above every number, so a NaN row would pass every "greater than" '
  'cut and count as up-regulated, while the browser, which receives the string '
  '"NaN", would count it as neither.';
COMMENT ON COLUMN public.scrna_de_genes.fdr IS
  'The p-value adjusted for multiple testing (Benjamini-Hochberg or Bonferroni), '
  'so never below pvalue.';
COMMENT ON COLUMN public.scrna_de_genes.pct_1 IS
  'Fraction, 0 to 1, of group1 cells expressing the gene.';
COMMENT ON COLUMN public.scrna_de_genes.pct_2 IS
  'Fraction, 0 to 1, of group2 cells expressing the gene.';
COMMENT ON COLUMN public.scrna_de_runs.params_hash IS
  'Identifies what the analysis ran on and with. Two runs of one dataset with '
  'the same hash are the same analysis. A batch load hashes its input files.';

COMMIT;
