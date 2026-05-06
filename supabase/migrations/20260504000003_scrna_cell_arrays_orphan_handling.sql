-- Migration: scrna_cell_arrays_orphan_handling
--
-- Three changes that close a class of silent scientific-correctness bugs in
-- the scRNA UMAP view when a cell's cluster_id has no matching catalog row
-- in scrna_clusters.
--
-- 1. Reserve cluster ordinal 255 as the orphan sentinel via a CHECK
--    constraint on scrna_clusters.ordinal. Real clusters get 0..254.
-- 2. Recreate scrna_cell_arrays() so orphan cells return ordinal=255 from
--    the RPC instead of NULL. The frontend then handles 255 explicitly
--    (gray + warning) rather than relying on JS null-coercion.
-- 3. Add a NOT VALID composite foreign key from scrna_cells (dataset_id,
--    cluster_id) to scrna_clusters (dataset_id, cluster_id). NOT VALID
--    applies to new writes only and skips validation of existing rows, so
--    the migration is safe to run against data that may already contain
--    orphans. A future VALIDATE CONSTRAINT step can run after a one-time
--    cleanup once we know prod state.

BEGIN;

-- 1. Reserve ordinal 255 as the orphan sentinel. NOT VALID so the migration
--    cannot fail on legacy rows; new writes are still checked.
ALTER TABLE public.scrna_clusters
  DROP CONSTRAINT IF EXISTS scrna_clusters_ordinal_below_sentinel;
ALTER TABLE public.scrna_clusters
  ADD CONSTRAINT scrna_clusters_ordinal_below_sentinel
  CHECK (ordinal < 255) NOT VALID;

-- 2. RPC returns 255 for orphan cells instead of NULL.
CREATE OR REPLACE FUNCTION public.scrna_cell_arrays(
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

-- 3. NOT VALID composite FK on (dataset_id, cluster_id).
ALTER TABLE public.scrna_cells
  DROP CONSTRAINT IF EXISTS scrna_cells_cluster_fkey;
ALTER TABLE public.scrna_cells
  ADD CONSTRAINT scrna_cells_cluster_fkey
  FOREIGN KEY (dataset_id, cluster_id)
  REFERENCES public.scrna_clusters (dataset_id, cluster_id)
  ON DELETE RESTRICT
  ON UPDATE CASCADE
  NOT VALID;

COMMIT;
