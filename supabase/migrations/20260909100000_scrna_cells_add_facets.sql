-- Migration: scrna_cells_add_facets
--
-- Let a cell carry a few named labels the explorer can filter on, beyond the
-- sample it came from.
--
-- The first use is transgene status: on the MYB41 dataset only 232 of 8,683
-- cells carry the construct, and they sit inside two of the three genotypes.
-- Colouring by the transgene shows them, but 34 lit dots among 2,937 is not
-- something a reader can pick out; being able to show those cells alone is.
--
-- JSONB on the cell rather than a table of its own, for the same reason the
-- sample is returned by the cell query: everything the view fetches is paired
-- with cells by position and carries no cell identifiers, so a second query
-- returning labels in its own order could attach every one of them to the wrong
-- cell and look entirely normal. One row, one query, nothing to get out of step.
--
-- Which columns become facets is the operator's choice at ingest. Nothing here
-- knows or cares what they are called.

BEGIN;

ALTER TABLE public.scrna_cells
  ADD COLUMN IF NOT EXISTS facets JSONB DEFAULT NULL;

-- A facet is a handful of short labels. An object of strings is the only shape
-- the explorer can render as toggles, and anything else -- an array, a number, a
-- nested object -- would arrive in the browser as something it cannot draw.
--
-- Through a function because a CHECK cannot hold a subquery, and walking the
-- object needs one. IMMUTABLE and pure: it reads only its argument.
CREATE OR REPLACE FUNCTION public.scrna_facets_are_flat_text(facets JSONB)
RETURNS BOOLEAN
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $fn$
  SELECT facets IS NULL
     OR (
       jsonb_typeof(facets) = 'object'
       AND NOT EXISTS (
         SELECT 1 FROM jsonb_each(facets) AS f(key, value)
         WHERE jsonb_typeof(f.value) <> 'string'
            OR btrim(f.key, E' \t\n\r\f\v\u00a0') = ''
            OR btrim(f.value #>> '{}', E' \t\n\r\f\v\u00a0') = ''
       )
     );
$fn$;

ALTER TABLE public.scrna_cells
  DROP CONSTRAINT IF EXISTS scrna_cells_facets_are_flat_text;
ALTER TABLE public.scrna_cells
  ADD CONSTRAINT scrna_cells_facets_are_flat_text
  CHECK (public.scrna_facets_are_flat_text(facets)) NOT VALID;

-- The return type changes, and CREATE OR REPLACE cannot do that.
DROP FUNCTION IF EXISTS public.scrna_cell_arrays(BIGINT);

CREATE FUNCTION public.scrna_cell_arrays(
  ds_id BIGINT
)
RETURNS TABLE (
  x               REAL,
  y               REAL,
  cluster_ordinal SMALLINT,
  replicate       TEXT,
  facets          JSONB
)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
  SELECT
    c.x::REAL,
    c.y::REAL,
    COALESCE(cl.ordinal, 255::SMALLINT) AS cluster_ordinal,
    c.replicate,
    c.facets
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
