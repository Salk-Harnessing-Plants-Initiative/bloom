-- Resolves where the search bar should navigate for an exact term. Returns one
-- jsonb object:
--   {"target": "species", "species_id": 7}
--   {"target": "plant", "species_id": 1, "experiment_id": 2, "wave_id": 3, "accession_id": 4}
--   {"target": "none"}
-- Ids only, never a URL — route shape stays in the client.
--
-- Priority: exact species name, then exact barcode, then exact accession. Each
-- candidate is resolved with DISTINCT ... LIMIT 2, so "exactly one destination"
-- is answered by the database instead of inferred from a capped row sample the
-- client dedupes in JS (see issue #528).

CREATE OR REPLACE FUNCTION cyl_plant_search_navigate(p_text text)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
  WITH term AS (
    SELECT btrim(COALESCE(p_text, '')) AS t
  ),
  -- Case-insensitive exact match, mirroring the client's previous ILIKE-with-
  -- escaped-wildcards call. Equality needs no escaping, so a term containing
  -- % or _ can no longer behave as a pattern here.
  species_hit AS (
    SELECT sp.id
    FROM species sp, term
    WHERE term.t <> ''
      AND lower(sp.common_name) = lower(term.t)
      AND sp.deleted_at IS NULL
    LIMIT 2
  ),
  -- A destination needs every id in the href; rows missing one contribute no
  -- destination, matching the client's old .filter(Boolean) on the built href.
  destinations AS (
    SELECT DISTINCT s.species_id, s.experiment_id, s.wave_id, s.accession_id, 'barcode' AS via
    FROM cyl_plant_search s, term
    WHERE term.t <> ''
      AND s.qr_code = term.t
      AND s.species_id IS NOT NULL AND s.experiment_id IS NOT NULL
      AND s.wave_id IS NOT NULL AND s.accession_id IS NOT NULL
    LIMIT 2
  ),
  accession_destinations AS (
    SELECT DISTINCT s.species_id, s.experiment_id, s.wave_id, s.accession_id, 'accession' AS via
    FROM cyl_plant_search s, term
    WHERE term.t <> ''
      AND s.accession_name = term.t
      AND s.species_id IS NOT NULL AND s.experiment_id IS NOT NULL
      AND s.wave_id IS NOT NULL AND s.accession_id IS NOT NULL
    LIMIT 2
  ),
  resolved AS (
    SELECT * FROM destinations
    WHERE (SELECT count(*) FROM species_hit) <> 1
      AND (SELECT count(*) FROM destinations) = 1
    UNION ALL
    SELECT * FROM accession_destinations
    WHERE (SELECT count(*) FROM species_hit) <> 1
      AND (SELECT count(*) FROM destinations) <> 1
      AND (SELECT count(*) FROM accession_destinations) = 1
  )
  SELECT CASE
    WHEN (SELECT count(*) FROM species_hit) = 1 THEN
      jsonb_build_object('target', 'species', 'species_id', (SELECT id FROM species_hit))
    WHEN (SELECT count(*) FROM resolved) = 1 THEN
      (SELECT jsonb_build_object(
                'target', 'plant',
                'species_id', r.species_id,
                'experiment_id', r.experiment_id,
                'wave_id', r.wave_id,
                'accession_id', r.accession_id)
       FROM resolved r)
    ELSE jsonb_build_object('target', 'none')
  END;
$$;

REVOKE ALL ON FUNCTION cyl_plant_search_navigate(text) FROM PUBLIC;
-- Supabase default privileges grant EXECUTE on public functions to anon; this
-- must not be callable unauthenticated, matching the view's own posture.
REVOKE ALL ON FUNCTION cyl_plant_search_navigate(text) FROM anon;
GRANT EXECUTE ON FUNCTION cyl_plant_search_navigate(text) TO authenticated;
GRANT EXECUTE ON FUNCTION cyl_plant_search_navigate(text) TO bloom_user;
GRANT EXECUTE ON FUNCTION cyl_plant_search_navigate(text) TO bloom_admin;
GRANT EXECUTE ON FUNCTION cyl_plant_search_navigate(text) TO bloom_agent;
