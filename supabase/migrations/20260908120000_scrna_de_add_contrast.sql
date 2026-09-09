-- Give differential expression a contrast dimension: which two groups were
-- compared, how many cells each had, and the summary the selector shows before
-- fetching a result file.
--
-- Existing rows are one-vs-rest and keep a NULL contrast, which is what NULL
-- means here. Nothing reads these columns yet.

BEGIN;

-- 1. The comparison ---------------------------------------------------------

ALTER TABLE public.scrna_de
  ADD COLUMN IF NOT EXISTS contrast TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS group1   TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS group2   TEXT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_group1 INT  DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_group2 INT  DEFAULT NULL;

COMMENT ON COLUMN public.scrna_de.contrast IS
  'Label for the comparison, e.g. pFACT_vs_Col-0. NULL means one-vs-rest: the '
  'cluster against every other cell in the dataset, which is what every row '
  'predating this column is.';
COMMENT ON COLUMN public.scrna_de.group1 IS
  'The side a positive log fold change belongs to. NULL for one-vs-rest.';
COMMENT ON COLUMN public.scrna_de.group2 IS
  'The side compared against. NULL for one-vs-rest.';
COMMENT ON COLUMN public.scrna_de.n_group1 IS
  'Cells behind group1. Small numbers here are why a result may be thin.';
COMMENT ON COLUMN public.scrna_de.n_group2 IS
  'Cells behind group2.';

-- 2. The outcome ------------------------------------------------------------

ALTER TABLE public.scrna_de
  ADD COLUMN IF NOT EXISTS n_genes_tested        INT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_significant_fdr     INT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_significant_fdr_lfc INT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_up                  INT DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS n_down                INT DEFAULT NULL;

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

-- 3. A comparison that never ran is a row, not an absence -------------------
-- Stored with its group sizes and no file, so the panel can say why.

ALTER TABLE public.scrna_de
  ALTER COLUMN file_path DROP NOT NULL;

-- 4. Invariants -------------------------------------------------------------
-- Every existing row already satisfies these.

-- A two-group result with the label left off would be served as cluster
-- markers, because a NULL contrast is what marks a row one-vs-rest.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_contrast_names_both_groups;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_contrast_names_both_groups
  CHECK (num_nonnulls(contrast, group1, group2) IN (0, 3));

-- A group compared against itself is not a comparison.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_groups_differ;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_groups_differ
  CHECK (group1 IS NULL OR group1 <> group2);

-- No file means nothing was tested. Naming only n_genes_tested is enough: the
-- arithmetic rules below force the other four to zero with it.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_no_file_means_nothing_tested;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_no_file_means_nothing_tested
  CHECK (
    file_path IS NOT NULL
    OR (contrast IS NOT NULL AND COALESCE(n_genes_tested, 0) = 0)
  );

-- All five or none. A CHECK comparing a NULL yields NULL, which passes -- so
-- one absent count would switch off every rule below it.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_counts_all_or_none;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_counts_all_or_none
  CHECK (num_nonnulls(n_genes_tested, n_significant_fdr,
                      n_significant_fdr_lfc, n_up, n_down) IN (0, 5));

-- A summary that contradicts itself. One rule per constraint, because Postgres
-- reports only the name that failed. Checking it against the result file is the
-- ingest's job; Postgres cannot read storage.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_counts_consistent;

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_significant_within_tested;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_significant_within_tested
  CHECK (n_significant_fdr <= n_genes_tested);

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_lfc_cut_narrows_fdr_cut;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_lfc_cut_narrows_fdr_cut
  CHECK (n_significant_fdr_lfc <= n_significant_fdr);

-- A gene clearing a fold-change cut moved up or down; there is no third bucket.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_up_plus_down_is_lfc_significant;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_up_plus_down_is_lfc_significant
  CHECK (n_up + n_down = n_significant_fdr_lfc);

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_counts_non_negative;
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

-- NULL is the only way to say nothing: an empty contrast reads as one-vs-rest.
-- The characters are spelled out because btrim() with one argument strips
-- spaces alone, and \s is locale-dependent -- it matches a non-breaking space
-- under en_US.UTF-8 but not under C, so the same row would be accepted on one
-- server and refused on another.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_text_not_blank;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_text_not_blank
  CHECK (
    (file_path IS NULL OR btrim(file_path, E' \t\n\r\f\v\u00a0') <> '') AND
    (contrast IS NULL OR btrim(contrast, E' \t\n\r\f\v\u00a0') <> '') AND
    (group1 IS NULL OR btrim(group1, E' \t\n\r\f\v\u00a0') <> '') AND
    (group2 IS NULL OR btrim(group2, E' \t\n\r\f\v\u00a0') <> '') AND
    (cluster_id IS NULL OR btrim(cluster_id, E' \t\n\r\f\v\u00a0') <> '')
  );

-- cluster_id and contrast index below, where an oversized value fails with a
-- btree row-size error rather than anything a reader could act on.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_name_lengths;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_name_lengths
  CHECK (
    length(cluster_id) <= 200 AND
    length(contrast)   <= 200 AND
    length(group1)     <= 100 AND
    length(group2)     <= 100
  );

-- Both sizes or neither, and only on a real comparison: "the rest" is not a
-- group with a size.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_group_sizes_all_or_none;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_group_sizes_all_or_none
  CHECK (num_nonnulls(n_group1, n_group2) IN (0, 2)
         AND (contrast IS NOT NULL OR num_nonnulls(n_group1, n_group2) = 0));

-- A skipped comparison stores five zeros, not five blanks: under blanks the
-- test for one -- n_genes_tested = 0 AND file_path IS NULL -- yields NULL
-- rather than true, and the row cannot be found.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_contrast_rows_carry_counts;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_contrast_rows_carry_counts
  CHECK (contrast IS NULL
         OR num_nonnulls(n_genes_tested, n_significant_fdr,
                         n_significant_fdr_lfc, n_up, n_down) = 5);

-- One row per comparison. NULLS NOT DISTINCT (Postgres 15) or the one-vs-rest
-- rows, whose contrast is NULL, stay unconstrained -- and those are the rows
-- every existing reader fetches.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_comparison_uniqueness;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_comparison_uniqueness
  UNIQUE NULLS NOT DISTINCT (dataset_id, cluster_id, contrast);

-- 5. Lookup -----------------------------------------------------------------
-- The uniqueness index above covers every lookup the older
-- (dataset_id, cluster_id) index served. Keeping both costs a write per insert.

DROP INDEX IF EXISTS public.idx_scrna_de_dataset_cluster;

-- 6. Role policies ----------------------------------------------------------
-- Every other scrna_* table got these in 20260506000001; this one was missed,
-- so bloom_user reads of DE return nothing today.
--
-- Policies only. The grants already exist from 20260414002000, and re-stating
-- them would risk undoing the later narrowings in 20260504000002 and
-- 20260710000000.

DROP POLICY IF EXISTS admin_all_scrna_de ON public.scrna_de;
CREATE POLICY admin_all_scrna_de
  ON public.scrna_de FOR ALL TO bloom_admin USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS agent_read_scrna_de ON public.scrna_de;
CREATE POLICY agent_read_scrna_de
  ON public.scrna_de FOR SELECT TO bloom_agent USING (true);

DROP POLICY IF EXISTS user_read_scrna_de ON public.scrna_de;
CREATE POLICY user_read_scrna_de
  ON public.scrna_de FOR SELECT TO bloom_user USING (true);

COMMIT;
