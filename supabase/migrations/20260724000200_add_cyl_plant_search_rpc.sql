-- Server-side advanced plant search. One call returns the page of matches, the
-- true total (so the UI can say "showing 500 of 5000"), and which pasted
-- barcodes do not exist. security_invoker so RLS on cyl_plant_search applies
-- with the caller's role. Empty array = field not filtered; AND across fields,
-- OR within a field.

CREATE OR REPLACE FUNCTION cyl_plant_search_query(
  p_barcodes       text[]   DEFAULT '{}',
  p_accession_ids  bigint[] DEFAULT '{}',
  p_species_ids    bigint[] DEFAULT '{}',
  p_experiment_ids bigint[] DEFAULT '{}',
  p_limit          integer  DEFAULT 500
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  -- The UI always asks for MAX_ROWS, but any holder of a bloom-role JWT can call
  -- this RPC directly through PostgREST, so the page size is clamped here rather
  -- than trusted from the caller.
  max_rows   constant integer := 500;
  max_filter constant integer := 5000;
  eff_limit  integer;
BEGIN
  -- A NULL array from a direct caller would make every filter test NULL and
  -- return nothing; treat it as "field not filtered", same as an empty array.
  p_barcodes       := COALESCE(p_barcodes, '{}');
  p_accession_ids  := COALESCE(p_accession_ids, '{}');
  p_species_ids    := COALESCE(p_species_ids, '{}');
  p_experiment_ids := COALESCE(p_experiment_ids, '{}');

  -- Reject an oversized filter instead of truncating it: a silently dropped
  -- barcode comes back as "not found", the exact false negative this RPC exists
  -- to prevent.
  IF cardinality(p_barcodes) > max_filter
     OR cardinality(p_accession_ids) > max_filter
     OR cardinality(p_species_ids) > max_filter
     OR cardinality(p_experiment_ids) > max_filter THEN
    RAISE EXCEPTION 'filter list too large (max % entries per field)', max_filter;
  END IF;

  eff_limit := LEAST(GREATEST(COALESCE(p_limit, max_rows), 0), max_rows);

  RETURN (
    WITH filtered AS (
      SELECT s.*
      FROM cyl_plant_search s
      WHERE (cardinality(p_barcodes)       = 0 OR s.qr_code       = ANY (p_barcodes))
        AND (cardinality(p_accession_ids)  = 0 OR s.accession_id  = ANY (p_accession_ids))
        AND (cardinality(p_species_ids)    = 0 OR s.species_id    = ANY (p_species_ids))
        AND (cardinality(p_experiment_ids) = 0 OR s.experiment_id = ANY (p_experiment_ids))
    ),
    page AS (
      SELECT *
      FROM filtered
      ORDER BY species_name NULLS LAST, qr_code
      LIMIT eff_limit
    ),
    -- Barcodes present at all in the RLS-scoped data, ignoring the other filters:
    -- "not found" means the barcode does not exist, not "excluded by a filter".
    existing AS (
      SELECT DISTINCT qr_code
      FROM cyl_plant_search
      WHERE qr_code = ANY (p_barcodes)
    )
    SELECT jsonb_build_object(
      'total', (SELECT count(*) FROM filtered),
      'rows',  COALESCE((SELECT jsonb_agg(to_jsonb(page)) FROM page), '[]'::jsonb),
      'not_found', COALESCE(
        (SELECT jsonb_agg(DISTINCT b ORDER BY b)
         FROM unnest(p_barcodes) AS b
         WHERE b NOT IN (SELECT qr_code FROM existing)),
        '[]'::jsonb)
    )
  );
END;
$$;

REVOKE ALL ON FUNCTION cyl_plant_search_query(text[], bigint[], bigint[], bigint[], integer) FROM PUBLIC;
-- Supabase default privileges grant EXECUTE on public functions to anon; the
-- search RPC must not be callable unauthenticated, so revoke it explicitly.
REVOKE ALL ON FUNCTION cyl_plant_search_query(text[], bigint[], bigint[], bigint[], integer) FROM anon;
GRANT EXECUTE ON FUNCTION cyl_plant_search_query(text[], bigint[], bigint[], bigint[], integer) TO authenticated;
GRANT EXECUTE ON FUNCTION cyl_plant_search_query(text[], bigint[], bigint[], bigint[], integer) TO bloom_user;
GRANT EXECUTE ON FUNCTION cyl_plant_search_query(text[], bigint[], bigint[], bigint[], integer) TO bloom_admin;
GRANT EXECUTE ON FUNCTION cyl_plant_search_query(text[], bigint[], bigint[], bigint[], integer) TO bloom_agent;
