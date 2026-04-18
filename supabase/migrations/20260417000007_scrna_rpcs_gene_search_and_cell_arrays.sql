-- Migration: scrna_rpcs_gene_search_and_cell_arrays
-- Phase 1 of Expression Explorer (add-scrna-expression-schema), PR-2.
--
-- Two PostgREST-callable functions the UI consumes:
--
--   scrna_gene_search(ds_id, q, lim) — trigram-backed autocomplete over
--     scrna_genes. Uses idx_scrna_genes_gene_name_trgm under ILIKE prefix
--     match. Returns up to `lim` gene names alphabetically.
--
--   scrna_cell_arrays(ds_id) — single-call bulk fetch of per-cell
--     coordinates + cluster ordinal for a whole dataset. Replaces the
--     client-side 1000-row chunked loop in expression-scatterplot.tsx.
--     Row shape: (x REAL, y REAL, pc1..pc5 REAL, cluster_ordinal SMALLINT).
--     Ordered by cell_number ascending for deterministic row order.
--
-- Both SECURITY INVOKER so RLS on the underlying tables applies to the
-- caller's role. Granted to anon, authenticated, and bloom_agent — the
-- last is explicit because bloom_agent has SELECT-only and does not
-- inherit executes from authenticated via the default privileges block.

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
  TO anon, authenticated, bloom_agent;

CREATE OR REPLACE FUNCTION public.scrna_cell_arrays(
  ds_id BIGINT
)
RETURNS TABLE (
  x               REAL,
  y               REAL,
  pc1             REAL,
  pc2             REAL,
  pc3             REAL,
  pc4             REAL,
  pc5             REAL,
  cluster_ordinal SMALLINT
)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
  SELECT
    c.x::REAL,
    c.y::REAL,
    c.pc1,
    c.pc2,
    c.pc3,
    c.pc4,
    c.pc5,
    cl.ordinal AS cluster_ordinal
  FROM public.scrna_cells c
  LEFT JOIN public.scrna_clusters cl
    ON cl.dataset_id = c.dataset_id
   AND cl.cluster_id = c.cluster_id
  WHERE c.dataset_id = ds_id
  ORDER BY c.cell_number ASC;
$$;

GRANT EXECUTE ON FUNCTION public.scrna_cell_arrays(BIGINT)
  TO anon, authenticated, bloom_agent;

COMMIT;
