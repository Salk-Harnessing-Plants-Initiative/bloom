-- Manual rollback for 20260910120000_scrna_de_common_results.sql
--
-- Removes the run dimension from scrna_de and drops the two tables the
-- migration added. The one-vs-rest rows that predate runs come through
-- unchanged, because their values in every added column are NULL and they take
-- no gene rows.
--
-- Refuses to run if any row holds data this would destroy. Dropping
-- scrna_de_genes discards every per-gene result as surely as a DELETE would,
-- and unlike the objects it replaced there is no second copy anywhere. Nothing
-- automated runs these scripts: the only time one runs is by hand, against
-- tables someone has already filled.
--
-- Convention for this repo: a rollback that drops a table, drops a column or
-- deletes rows counts what it would destroy first and raises if that count is
-- not zero. 20260722000200_create_cyl_intermediates_bucket_rollback.sql set the
-- pattern; 20260908120000_scrna_de_add_contrast_rollback.sql follows it, and so
-- does this.
--
-- No grants are revoked. The migration adds none.

BEGIN;

DO $$
DECLARE
    at_risk bigint;
    gene_rows bigint;
BEGIN
    SELECT count(*) INTO at_risk
      FROM public.scrna_de
     WHERE run_id IS NOT NULL
        OR group_kind IS NOT NULL
        OR method IS NOT NULL
        OR params_hash IS NOT NULL
        OR tested IS NOT NULL;

    SELECT count(*) INTO gene_rows FROM public.scrna_de_genes;

    IF at_risk > 0 OR gene_rows > 0 THEN
        RAISE EXCEPTION
            'refusing to roll back: % scrna_de rows belong to an analysis and % '
            'per-gene rows would be destroyed. Export them, or remove the runs '
            'deliberately, before running this.',
            at_risk, gene_rows;
    END IF;
END $$;

-- Restore the uniqueness rule to the one that predates runs. Safe because the
-- guard above has established that every remaining row has run_id NULL, so no
-- two rows can collide on the narrower key that did not already collide.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_comparison_uniqueness;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_comparison_uniqueness
  UNIQUE NULLS NOT DISTINCT (dataset_id, cluster_id, contrast);

DROP INDEX IF EXISTS public.scrna_de_question_idx;
DROP INDEX IF EXISTS public.scrna_de_run_idx;

-- The rules that only made sense with runs.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_run_rows_are_complete,
  DROP CONSTRAINT IF EXISTS scrna_de_group_kind_known,
  DROP CONSTRAINT IF EXISTS scrna_de_untested_counted_nothing,
  DROP CONSTRAINT IF EXISTS scrna_de_result_is_somewhere,
  DROP CONSTRAINT IF EXISTS scrna_de_cluster_in_catalogue;

-- Restore the rule the migration replaced. Every surviving row names a file,
-- so this holds for all of them.
ALTER TABLE public.scrna_de
  DROP CONSTRAINT IF EXISTS scrna_de_no_file_means_nothing_tested;
ALTER TABLE public.scrna_de
  ADD CONSTRAINT scrna_de_no_file_means_nothing_tested
  CHECK (
    file_path IS NOT NULL
    OR (contrast IS NOT NULL AND COALESCE(n_genes_tested, 0) = 0)
  );

ALTER TABLE public.scrna_de
  DROP COLUMN IF EXISTS run_id,
  DROP COLUMN IF EXISTS group_kind,
  DROP COLUMN IF EXISTS method,
  DROP COLUMN IF EXISTS params_hash,
  DROP COLUMN IF EXISTS tested;

DROP TABLE IF EXISTS public.scrna_de_genes;
DROP TABLE IF EXISTS public.scrna_de_runs;

-- Added by the migration purely so scrna_de_genes could reference
-- (dataset_id, id). Nothing else depends on it.
ALTER TABLE public.scrna_genes
  DROP CONSTRAINT IF EXISTS scrna_genes_dataset_gene_key;

COMMIT;
