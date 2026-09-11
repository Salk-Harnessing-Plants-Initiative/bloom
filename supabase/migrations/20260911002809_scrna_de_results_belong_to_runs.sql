-- Drop the four DE threshold counts; new contrasts belong to a run.

BEGIN;

-- 1. Threshold counts ------------------------------------------------------------
-- Each was an answer at an unrecorded cut; the gene rows replace them.

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_counts_all_or_none,
  DROP CONSTRAINT IF EXISTS scrna_de_counts_non_negative,
  DROP CONSTRAINT IF EXISTS scrna_de_significant_within_tested,
  DROP CONSTRAINT IF EXISTS scrna_de_lfc_cut_narrows_fdr_cut,
  DROP CONSTRAINT IF EXISTS scrna_de_up_plus_down_is_lfc_significant,
  DROP CONSTRAINT IF EXISTS scrna_de_contrast_rows_carry_counts;

ALTER TABLE public.scrna_de
  DROP COLUMN IF EXISTS n_significant_fdr,
  DROP COLUMN IF EXISTS n_significant_fdr_lfc,
  DROP COLUMN IF EXISTS n_up,
  DROP COLUMN IF EXISTS n_down;

-- Group sizes and genes tested are never negative.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_sizes_non_negative;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_sizes_non_negative
  CHECK (n_group1 >= 0 AND n_group2 >= 0 AND n_genes_tested >= 0);

-- A run row says how many genes it tested; a tested one tested some.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_run_rows_count_their_genes,
  DROP CONSTRAINT IF EXISTS scrna_de_tested_means_genes_tested;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_run_rows_count_their_genes
  CHECK (run_id IS NULL OR n_genes_tested IS NOT NULL);
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_tested_means_genes_tested
  CHECK (tested IS NOT TRUE OR n_genes_tested > 0);

COMMENT ON COLUMN public.scrna_de.n_genes_tested IS
  'Genes the comparison tested. On a run row, 0 when untested.';


-- 2. Runs -------------------------------------------------------------------------

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_run_metadata_needs_a_run,
  DROP CONSTRAINT IF EXISTS scrna_de_run_rows_name_no_file,
  DROP CONSTRAINT IF EXISTS scrna_de_new_contrasts_belong_to_a_run;

-- Analysis columns need a run.
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_run_metadata_needs_a_run
  CHECK (run_id IS NOT NULL
         OR num_nonnulls(group_kind, method, params_hash, tested) = 0);

-- A run row keeps its genes as rows, not in a file.
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_run_rows_name_no_file
  CHECK (run_id IS NULL OR file_path IS NULL);

-- A new contrast belongs to a run; NOT VALID keeps contrasts loaded before runs.
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_new_contrasts_belong_to_a_run
  CHECK (run_id IS NOT NULL OR contrast IS NULL) NOT VALID;


-- 3. Left open by 20260911000000 -----------------------------------------------

-- The orphan check returned 0 on staging and production.
ALTER TABLE public.scrna_de VALIDATE CONSTRAINT scrna_de_cluster_in_catalogue;

-- Explicit, so they don't depend on the migrating role's default privileges.
GRANT SELECT ON public.scrna_de_runs, public.scrna_de_genes TO bloom_user, bloom_agent;
GRANT SELECT, INSERT ON public.scrna_de_runs, public.scrna_de_genes TO bloom_writer;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON public.scrna_de_runs, public.scrna_de_genes TO bloom_admin;

COMMENT ON TABLE public.scrna_de_genes IS
  'One row per gene a comparison tested. Rankings and counts come from these '
  'rows, at any cut at least as strict as the analysis''s own filter.';
COMMENT ON CONSTRAINT scrna_de_genes_fold_change_is_a_number_or_nothing
  ON public.scrna_de_genes IS
  'Postgres ranks NaN above every number, so a NaN fold change would pass every '
  'greater-than cut. One that could not be computed is NULL.';
COMMENT ON COLUMN public.scrna_de_genes.fdr IS
  'Adjusted p-value (Benjamini-Hochberg, Bonferroni, Holm or BY); never below pvalue.';
COMMENT ON COLUMN public.scrna_de_genes.pct_1 IS
  'Fraction, 0 to 1, of group1 cells expressing the gene.';
COMMENT ON COLUMN public.scrna_de_genes.pct_2 IS
  'Fraction, 0 to 1, of group2 cells expressing the gene.';
COMMENT ON COLUMN public.scrna_de_runs.params_hash IS
  'Identifies what the analysis ran on and with; same dataset and hash is the '
  'same analysis. A batch load hashes its input files.';

COMMIT;
