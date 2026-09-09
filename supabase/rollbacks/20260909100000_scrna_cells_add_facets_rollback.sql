-- Rollback: scrna_cells_add_facets
--
-- Puts the cell query back without the facets column, and drops the column.
--
-- Dropping a column destroys what is in it, so this counts what it would take
-- with it and refuses rather than doing it quietly. Facets are loaded from the
-- source file, so they can be rebuilt by re-running the ingest -- but that is a
-- decision for whoever is undoing this, not something to discover afterwards.

BEGIN;

DO $$
DECLARE at_risk bigint;
BEGIN
  SELECT count(*) INTO at_risk FROM public.scrna_cells WHERE facets IS NOT NULL;
  IF at_risk > 0 THEN
    RAISE EXCEPTION
      'Refusing to roll back: % cell(s) carry facets that dropping the column '
      'would destroy. Re-running the cell ingest without --facet clears them '
      'deliberately, then this will run.', at_risk;
  END IF;
END $$;

DROP FUNCTION IF EXISTS public.scrna_cell_arrays(BIGINT);

CREATE FUNCTION public.scrna_cell_arrays(
  ds_id BIGINT
)
RETURNS TABLE (
  x               REAL,
  y               REAL,
  cluster_ordinal SMALLINT,
  replicate       TEXT
)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
  SELECT
    c.x::REAL,
    c.y::REAL,
    COALESCE(cl.ordinal, 255::SMALLINT) AS cluster_ordinal,
    c.replicate
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

COMMIT;
