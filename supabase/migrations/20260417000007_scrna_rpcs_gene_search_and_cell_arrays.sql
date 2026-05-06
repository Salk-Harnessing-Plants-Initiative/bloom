-- Migration: scrna_rpcs_gene_search_and_cell_arrays
-- Two PostgREST-callable functions the UI consumes:
--
--   scrna_gene_search(ds_id, q, lim) — gene-name autocomplete (prefix match).
--     Returns up to `lim` gene names alphabetically.
--
--   scrna_cell_arrays(ds_id) — fetch per-cell coordinates + cluster ordinal for a dataset.
--
-- Functions run with the caller's permissions (bloom_user, bloom_admin, bloom_agent).

BEGIN;

CREATE OR REPLACE FUNCTION public.scrna_gene_search(
  ds_id BIGINT,
  q     TEXT,
  lim   INT DEFAULT 20
)
RETURNS TABLE (gene_name TEXT)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
  SELECT g.gene_name
  FROM public.scrna_genes g
  WHERE g.dataset_id = ds_id
    AND g.gene_name ILIKE (q || '%')
  ORDER BY g.gene_name
  LIMIT lim;
$$;

GRANT EXECUTE ON FUNCTION public.scrna_gene_search(BIGINT, TEXT, INT)
  TO anon, authenticated, bloom_user, bloom_admin, bloom_agent;

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
    cl.ordinal AS cluster_ordinal
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
