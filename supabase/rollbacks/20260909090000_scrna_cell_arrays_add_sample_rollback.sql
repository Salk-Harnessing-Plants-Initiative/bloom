-- Rollback: scrna_cell_arrays_add_sample
--
-- Puts the function back as it was, without the sample column.
--
-- This deletes nothing and drops no column, so unlike the rollbacks that do, it
-- needs no guard against running on a populated table: the cells and their
-- samples are untouched either way, and only the shape of what the RPC returns
-- goes back.
--
-- The UMAP's sample toggles stop working after this, because the view has
-- nothing to filter on. That is the point of undoing it.

BEGIN;

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

COMMIT;
