-- Give differential expression a contrast dimension.
--
-- scrna_de keys results by cluster alone, which fits one-vs-rest -- a cluster
-- against every other cell -- and nothing else. A dataset with several
-- genotypes compares two named groups within a cell type, and that has
-- nowhere to go today.
--
-- This adds the two sides of the comparison, their cell counts, and the
-- per-combination summary the selector shows before downloading anything.
--
-- Existing rows are one-vs-rest and are not touched: contrast stays NULL,
-- which is what NULL means here.
--
-- No column records whether a comparison ran. Two already say it -- a row with
-- no file has nothing to open, and n_genes_tested is 0 -- and the CHECK below
-- keeps those two from disagreeing. A third flag would be one more thing to
-- keep in sync.
--
-- Nothing reads these columns yet. The ingest that writes them is a later
-- change.

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
-- Read by the selector so it can show "1 significant of 12,085 tested" without
-- fetching a multi-megabyte result file.

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
-- "Not run, because pHORST had 0 cells and Col-0 had 7" is an answer. A
-- missing dropdown entry is not, so such a combination is stored with its
-- group sizes and no file.

ALTER TABLE public.scrna_de
  ALTER COLUMN file_path DROP NOT NULL;

-- 4. Invariants -------------------------------------------------------------
-- Every existing row satisfies all of these: their new columns are NULL and
-- their file_path is set.

-- All three together, or none. A NULL contrast is what marks a row as
-- one-vs-rest, and every reader that wants only those rows tests it -- so a
-- two-group result with the label left off would be served as cluster markers.
-- The three columns describe one comparison; none of them may go missing on
-- its own.
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

-- What replaces a `tested` flag: no file means no result, so every count is
-- zero, not just the gene count. Without covering all of them a row can say it
-- never ran and still report 500 significant genes.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_no_file_means_nothing_tested;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_no_file_means_nothing_tested
  CHECK (
    file_path IS NOT NULL OR
    COALESCE(n_genes_tested, 0)
      + COALESCE(n_significant_fdr, 0)
      + COALESCE(n_significant_fdr_lfc, 0)
      + COALESCE(n_up, 0)
      + COALESCE(n_down, 0) = 0
  );

-- The five summary counts arrive together or not at all. Every check below
-- compares two of them, and a comparison with a NULL operand yields NULL, which
-- a CHECK accepts -- so without this rule one absent count switches off all of
-- them, and a half-written row is exactly what a mis-parsed column produces.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_counts_all_or_none;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_counts_all_or_none
  CHECK (num_nonnulls(n_genes_tested, n_significant_fdr,
                      n_significant_fdr_lfc, n_up, n_down) IN (0, 5));

-- Arithmetic true by definition of the counts. These cannot check the summary
-- against the result file -- Postgres cannot read storage, and the ingest that
-- holds both is where that belongs. They reject a summary that contradicts
-- itself. One rule per constraint, because Postgres reports only the name.
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

-- NULL is the only way to say nothing. An empty contrast is read as one-vs-rest
-- and rendered as a marker list; an empty file_path is rendered as a link that
-- goes nowhere. The regex covers every whitespace character, not just the space:
-- a stray tab or newline is what a mis-parsed TSV column produces, and btrim()
-- with one argument strips spaces alone.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_text_not_blank;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_text_not_blank
  CHECK (
    (file_path  IS NULL OR file_path  !~ '^\s*$') AND
    (contrast   IS NULL OR contrast   !~ '^\s*$') AND
    (group1     IS NULL OR group1     !~ '^\s*$') AND
    (group2     IS NULL OR group2     !~ '^\s*$') AND
    (cluster_id IS NULL OR cluster_id !~ '^\s*$')
  );

-- cluster_id and contrast are both columns of the uniqueness index below, so an
-- oversized value there fails the insert with a btree row-size error rather than
-- anything a reader could act on. group1 and group2 are bounded for consistency.
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

-- The two group sizes travel together, and belong only to a real comparison.
-- Same rule as the five counts above, applied to the pair it missed: a row
-- saying 164 cells on one side and nothing on the other is a half-written row,
-- and a one-vs-rest row has no groups to size.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_group_sizes_all_or_none;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_group_sizes_all_or_none
  CHECK (num_nonnulls(n_group1, n_group2) IN (0, 2)
         AND (contrast IS NOT NULL OR num_nonnulls(n_group1, n_group2) = 0));

-- A row that names a comparison carries all five counts, so a skipped one stores
-- five zeros rather than five blanks. Both spellings were legal before, and under
-- the blank one the documented test for a skipped comparison --
-- `n_genes_tested = 0 AND file_path IS NULL` -- yields NULL instead of true, so the
-- row cannot be found. The source summary leaves n_up and n_down empty on all 23
-- skipped rows, so this is the rule ingest has to read them through.
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
-- scrna_de_comparison_uniqueness above indexes (dataset_id, cluster_id,
-- contrast), which serves every lookup the older (dataset_id, cluster_id) index
-- from 20260417000001 did. Keeping both would cost a second write per insert
-- for nothing.

DROP INDEX IF EXISTS public.idx_scrna_de_dataset_cluster;

-- 6. Role policies ----------------------------------------------------------
-- 20260506000001 gave every scrna_* table bloom_admin / bloom_agent /
-- bloom_user policies except this one, so bloom_user reads of DE return no
-- rows today. Closing that here leaves the existing authenticated and anon
-- policies alone.
--
-- Policies only. The table grants already exist: 20260414002000 granted these
-- roles on ALL TABLES IN SCHEMA public, and scrna_de predates it. Re-stating
-- them here would only risk contradicting a later narrowing -- 20260504000002
-- strips TRUNCATE / REFERENCES / TRIGGER from bloom_admin, and 20260710000000
-- strips UPDATE from bloom_user -- which is what copying the pre-hardening
-- scrna_clusters block did.

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
