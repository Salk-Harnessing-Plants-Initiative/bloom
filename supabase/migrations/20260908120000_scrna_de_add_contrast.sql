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
-- Every existing row satisfies all three: their new columns are NULL and their
-- file_path is set.

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_contrast_names_both_groups;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_contrast_names_both_groups
  CHECK (contrast IS NULL OR (group1 IS NOT NULL AND group2 IS NOT NULL));

-- The pair that replaces a `tested` flag. Without this they could drift and
-- the UI would have to guess which one to believe.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_no_file_means_nothing_tested;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_no_file_means_nothing_tested
  CHECK (file_path IS NOT NULL OR COALESCE(n_genes_tested, 0) = 0);

ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_counts_non_negative;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_counts_non_negative
  CHECK (
    COALESCE(n_group1, 0)              >= 0 AND
    COALESCE(n_group2, 0)              >= 0 AND
    COALESCE(n_genes_tested, 0)        >= 0 AND
    COALESCE(n_significant_fdr, 0)     >= 0 AND
    COALESCE(n_significant_fdr_lfc, 0) >= 0 AND
    COALESCE(n_up, 0)                  >= 0 AND
    COALESCE(n_down, 0)                >= 0
  );

-- 5. Lookup -----------------------------------------------------------------
-- 20260417000001 indexes (dataset_id, cluster_id). Selecting one combination
-- now also names the contrast.

CREATE INDEX IF NOT EXISTS idx_scrna_de_dataset_cluster_contrast
  ON public.scrna_de (dataset_id, cluster_id, contrast);

-- 6. Role policies and grants -----------------------------------------------
-- 20260506000001 gave every scrna_* table bloom_admin / bloom_agent /
-- bloom_user policies except this one, so bloom_user reads of DE return no
-- rows today. Closing that here, matching the pattern used for
-- scrna_clusters, leaves the existing authenticated and anon policies alone.

DROP POLICY IF EXISTS admin_all_scrna_de ON public.scrna_de;
CREATE POLICY admin_all_scrna_de
  ON public.scrna_de FOR ALL TO bloom_admin USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS agent_read_scrna_de ON public.scrna_de;
CREATE POLICY agent_read_scrna_de
  ON public.scrna_de FOR SELECT TO bloom_agent USING (true);

DROP POLICY IF EXISTS user_read_scrna_de ON public.scrna_de;
CREATE POLICY user_read_scrna_de
  ON public.scrna_de FOR SELECT TO bloom_user USING (true);

-- Explicit grants: ALTER DEFAULT PRIVILEGES in 20260414002000 only covers
-- objects created by the role that ran it, so it cannot be relied on here.
GRANT SELECT, INSERT, UPDATE ON public.scrna_de TO bloom_user;
GRANT ALL                    ON public.scrna_de TO bloom_admin;
GRANT SELECT                 ON public.scrna_de TO bloom_agent;

GRANT USAGE, SELECT ON SEQUENCE public.scrna_de_id_seq TO bloom_user, bloom_admin;
GRANT USAGE         ON SEQUENCE public.scrna_de_id_seq TO bloom_agent;

COMMIT;
