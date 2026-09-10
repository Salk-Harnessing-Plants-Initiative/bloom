-- Rollback: scrna_cells_genotype_and_labels
--
-- Puts the cell query back to position and cluster alone, unpoints the cells,
-- and drops what this added.
--
-- Dropping a table and two columns destroys what is in them, so this counts what
-- it would take and refuses rather than doing it quietly. Both are rebuilt by
-- re-running the cell ingest, but that is a decision for whoever is undoing
-- this, not something to discover afterwards.
--
-- `scrna_cells.replicate` is untouched throughout. It holds whatever it held
-- before, which is why undoing this leaves the map with something to filter on
-- rather than nothing.

BEGIN;

DO $$
DECLARE genotypes bigint; labelled bigint; sourced bigint;
BEGIN
  SELECT count(*) INTO genotypes FROM public.scrna_genotypes;
  SELECT count(*) INTO labelled  FROM public.scrna_cells WHERE facets IS NOT NULL;
  SELECT count(*) INTO sourced   FROM public.scrna_clusters WHERE source IS NOT NULL;
  IF genotypes > 0 OR labelled > 0 OR sourced > 0 THEN
    RAISE EXCEPTION
      'Refusing to roll back: % genotype row(s), % cell(s) carrying labels and '
      '% cell type(s) recording a label source would be destroyed, along with '
      'every cell''s link to its genotype. Re-running the cell ingest rebuilds '
      'them from the source file. To clear them by hand, NULL '
      'scrna_cells.genotype_id first -- the foreign key refuses to drop a '
      'genotype while a cell points at it.', genotypes, labelled, sourced;
  END IF;
END $$;

DROP FUNCTION IF EXISTS public.scrna_cell_arrays(BIGINT);

CREATE FUNCTION public.scrna_cell_arrays(
  ds_id BIGINT
)
RETURNS TABLE (
  x               REAL,
  y               REAL,
  cluster_ordinal SMALLINT
)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
  SELECT
    c.x::REAL,
    c.y::REAL,
    COALESCE(cl.ordinal, 255::SMALLINT) AS cluster_ordinal
  FROM public.scrna_cells c
  LEFT JOIN public.scrna_clusters cl
    ON cl.dataset_id = c.dataset_id
   AND cl.cluster_id = c.cluster_id
  WHERE c.dataset_id = ds_id
  ORDER BY c.cell_number ASC;
$$;

GRANT EXECUTE ON FUNCTION public.scrna_cell_arrays(BIGINT)
  TO anon, authenticated, bloom_user, bloom_admin, bloom_agent;

ALTER TABLE public.scrna_cells
  DROP CONSTRAINT IF EXISTS scrna_cells_facets_are_flat_text;
ALTER TABLE public.scrna_cells DROP COLUMN IF EXISTS facets;
DROP FUNCTION IF EXISTS public.scrna_facets_are_flat_text(JSONB);

ALTER TABLE public.scrna_cells
  DROP CONSTRAINT IF EXISTS scrna_cells_genotype_fkey;
DROP INDEX IF EXISTS public.idx_scrna_cells_genotype;
ALTER TABLE public.scrna_cells DROP COLUMN IF EXISTS genotype_id;

DROP TABLE IF EXISTS public.scrna_genotypes;

-- The third column this added.
ALTER TABLE public.scrna_clusters DROP COLUMN IF EXISTS source;

COMMIT;
