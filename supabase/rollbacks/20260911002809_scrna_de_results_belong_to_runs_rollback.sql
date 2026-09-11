-- Manual rollback for 20260911002809_scrna_de_results_belong_to_runs.sql.
-- The count columns come back empty: what they held was dropped with them. The
-- rule demanding counts on a contrast row comes back NOT VALID for that reason.
-- The explicit grants and the validated cell-type key stay: the grants match
-- the defaults they restate, and a key cannot be un-validated.

BEGIN;

DROP TRIGGER IF EXISTS scrna_clusters_keep_their_dataset ON public.scrna_clusters;
DROP FUNCTION IF EXISTS public.scrna_clusters_keep_their_dataset();

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_run_metadata_needs_a_run,
  DROP CONSTRAINT IF EXISTS scrna_de_run_rows_name_no_file,
  DROP CONSTRAINT IF EXISTS scrna_de_new_contrasts_belong_to_a_run,
  DROP CONSTRAINT IF EXISTS scrna_de_group_sizes_non_negative;

ALTER TABLE public.scrna_de
  ADD COLUMN IF NOT EXISTS n_genes_tested INT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_significant_fdr INT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_significant_fdr_lfc INT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_up INT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_down INT DEFAULT NULL;

COMMENT ON COLUMN public.scrna_de.n_genes_tested IS
  'Genes the comparison covered. 0 together with a NULL file_path means it was '
  'never run -- typically too few cells on one side.';
COMMENT ON COLUMN public.scrna_de.n_significant_fdr IS
  'Genes passing the FDR cut alone.';
COMMENT ON COLUMN public.scrna_de.n_significant_fdr_lfc IS
  'Genes passing both the FDR and the fold-change cut.';
COMMENT ON COLUMN public.scrna_de.n_up IS
  'Of the genes passing both cuts, how many are higher in group1.';
COMMENT ON COLUMN public.scrna_de.n_down IS
  'Of the genes passing both cuts, how many are lower in group1.';

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_counts_all_or_none,
  DROP CONSTRAINT IF EXISTS scrna_de_counts_non_negative,
  DROP CONSTRAINT IF EXISTS scrna_de_significant_within_tested,
  DROP CONSTRAINT IF EXISTS scrna_de_lfc_cut_narrows_fdr_cut,
  DROP CONSTRAINT IF EXISTS scrna_de_up_plus_down_is_lfc_significant,
  DROP CONSTRAINT IF EXISTS scrna_de_contrast_rows_carry_counts,
  DROP CONSTRAINT IF EXISTS scrna_de_untested_counted_nothing;

ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_counts_all_or_none
  CHECK (num_nonnulls(n_genes_tested, n_significant_fdr,
                      n_significant_fdr_lfc, n_up, n_down) IN (0, 5));
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_counts_non_negative
  CHECK (
    n_group1              >= 0 AND
    n_group2              >= 0 AND
    n_genes_tested        >= 0 AND
    n_significant_fdr     >= 0 AND
    n_significant_fdr_lfc >= 0 AND
    n_up                  >= 0 AND
    n_down                >= 0
  );
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_significant_within_tested
  CHECK (n_significant_fdr <= n_genes_tested);
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_lfc_cut_narrows_fdr_cut
  CHECK (n_significant_fdr_lfc <= n_significant_fdr);
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_up_plus_down_is_lfc_significant
  CHECK (n_up + n_down = n_significant_fdr_lfc);
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_untested_counted_nothing
  CHECK (tested IS NOT FALSE OR COALESCE(n_genes_tested, 0) = 0);
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_contrast_rows_carry_counts
  CHECK (contrast IS NULL
         OR num_nonnulls(n_genes_tested, n_significant_fdr,
                         n_significant_fdr_lfc, n_up, n_down) = 5) NOT VALID;

COMMIT;
