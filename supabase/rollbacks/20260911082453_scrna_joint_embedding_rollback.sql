-- Manual rollback for 20260911082453_scrna_joint_embedding.sql
--
-- Drops the four embedding tables, the two read functions, the point-label check,
-- the three guard functions with their triggers, the per-dataset uniqueness on
-- scrna_cells and scrna_datasets.kind.
--
-- Refuses to run while any embedding or reference dataset exists. Dropping the
-- tables discards every loaded map, and dropping kind would quietly turn each
-- reference into an ordinary dataset with no cells, which the explorer would
-- list as a map it cannot draw. Remove those deliberately first.

BEGIN;

DO $$
DECLARE
  embedding_count BIGINT := 0;
  reference_count BIGINT := 0;
BEGIN
  IF to_regclass('public.scrna_embeddings') IS NOT NULL THEN
    SELECT count(*) INTO embedding_count FROM public.scrna_embeddings;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'public' AND table_name = 'scrna_datasets'
                AND column_name = 'kind') THEN
    EXECUTE 'SELECT count(*) FROM public.scrna_datasets WHERE kind = ''reference'''
      INTO reference_count;
  END IF;
  IF embedding_count > 0 OR reference_count > 0 THEN
    RAISE EXCEPTION
      'refusing to roll back: % joint embeddings and % reference datasets would be '
      'lost. To inspect them: SELECT id, name FROM public.scrna_embeddings; SELECT '
      'id, name FROM public.scrna_datasets WHERE kind = ''reference''. To remove an '
      'embedding: DELETE FROM public.scrna_embeddings WHERE id = $1 (its members, '
      'labels and points go with it).',
      embedding_count, reference_count;
  END IF;
END $$;

DROP FUNCTION IF EXISTS public.scrna_embedding_label_codes(BIGINT, TEXT);
DROP FUNCTION IF EXISTS public.scrna_embedding_arrays(BIGINT);

DROP TABLE IF EXISTS public.scrna_embedding_points;
DROP FUNCTION IF EXISTS public.scrna_embedding_point_labels_ok(JSONB);
DROP TABLE IF EXISTS public.scrna_embedding_labels;
DROP TABLE IF EXISTS public.scrna_embedding_members;
DROP TABLE IF EXISTS public.scrna_embeddings;
DROP FUNCTION IF EXISTS public.scrna_embeddings_check_finished();

DROP TRIGGER IF EXISTS scrna_datasets_guard_embedding_rules ON public.scrna_datasets;
DROP FUNCTION IF EXISTS public.scrna_datasets_guard_embedding_rules();

DROP TRIGGER IF EXISTS scrna_cells_refuse_reference_dataset ON public.scrna_cells;
DROP FUNCTION IF EXISTS public.scrna_cells_refuse_reference_dataset();

ALTER TABLE public.scrna_cells DROP CONSTRAINT IF EXISTS scrna_cells_id_per_dataset;

ALTER TABLE public.scrna_datasets DROP CONSTRAINT IF EXISTS scrna_datasets_kind_valid;
ALTER TABLE public.scrna_datasets DROP COLUMN IF EXISTS kind;

COMMIT;
