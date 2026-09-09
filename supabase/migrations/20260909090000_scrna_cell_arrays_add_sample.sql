-- Migration: scrna_cell_arrays_add_sample
--
-- Return each cell's sample alongside its position, so the UMAP can show one
-- sample at a time.
--
-- It comes from the same query rather than a second one on purpose. The view
-- pairs everything with cells by position and nothing else -- there are no cell
-- identifiers in what it fetches -- so a separate call returning samples in its
-- own order could attach every label to the wrong cell and look entirely
-- normal. One query, one ORDER BY, no pairing to get wrong.
--
-- The column is added rather than replacing anything, and the only caller reads
-- the columns by name, so nothing that reads this today changes.
--
-- `replicate` is the column's name in scrna_cells. It holds the sample a cell
-- came from -- Col-0, pFACT, pHORST on the first dataset to use it.

BEGIN;

-- The return type changes, and CREATE OR REPLACE cannot do that.
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

COMMIT;
