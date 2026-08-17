-- Migration: add_gravi_experiment_search
-- Server-side name search for gravi_experiments, backing `bloomctl plate download
-- --experiment-name`. The gravi-side analog of cyl_experiment_search: the CLI can't fetch
-- every experiment to filter client-side once the table is large, and an ILIKE '%term%'
-- pattern can't use a normal index, so a pg_trgm GIN index on the name makes the substring
-- search index-backed.
--
-- The RPC is SECURITY INVOKER (RLS still applies) and takes the query as a bound parameter
-- used in an ILIKE expression (not dynamic SQL), so the caller's text is only ever data,
-- never SQL — no input needs to be inspected for "code".
--
-- Two differences from cyl_experiment_search:
--
-- 1. No deleted_at filter. gravi_experiments has no such column; nothing is soft-deleted yet.
--
-- 2. The result carries system_name. gravi_experiments is UNIQUE(species_id, name,
--    system_name), so the same experiment name on two GraviScan rigs is a legal, expected
--    state. Without system_name an ambiguous match would show rows that look identical and
--    the caller would have no basis to pick one.
--
-- Additive only: no existing columns or rows are modified.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_gravi_experiments_name_trgm
  ON public.gravi_experiments USING gin (name gin_trgm_ops);

-- Substring name search over plate experiments, trigram-indexed for scale. Returns the fields
-- the CLI shows on an ambiguous match (id, name, species, system, created). LIKE
-- metacharacters in p_query are escaped so it is a literal substring search, and a direct
-- PostgREST caller can't page past max_rows.
CREATE OR REPLACE FUNCTION public.gravi_experiment_search(
  p_query   text,
  p_species text    DEFAULT NULL,
  p_limit   integer DEFAULT 50
)
RETURNS TABLE (
  id           bigint,
  name         text,
  species_id   bigint,
  species_name text,
  system_name  text,
  created_at   timestamptz
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  max_rows  constant integer := 50;
  eff_limit integer;
  pattern   text;
BEGIN
  -- An empty query must never match every experiment.
  IF p_query IS NULL OR btrim(p_query) = '' THEN
    RETURN;
  END IF;
  IF length(p_query) > 200 THEN
    RAISE EXCEPTION 'search query too long (max 200 characters)';
  END IF;

  -- Clamp the page size here rather than trusting a direct PostgREST caller.
  eff_limit := LEAST(GREATEST(COALESCE(p_limit, max_rows), 1), max_rows);

  -- Escape LIKE metacharacters so the query is a literal substring, not a wildcard.
  pattern := '%' || replace(replace(replace(p_query, '\', '\\'), '%', '\%'), '_', '\_') || '%';

  RETURN QUERY
    SELECT e.id, e.name, e.species_id, s.common_name AS species_name,
           e.system_name, e.created_at
    FROM gravi_experiments e
    LEFT JOIN species s ON s.id = e.species_id
    WHERE e.name ILIKE pattern
      AND (p_species IS NULL OR lower(s.common_name) = lower(btrim(p_species)))
    ORDER BY e.name, e.system_name NULLS FIRST, e.id
    LIMIT eff_limit;
END;
$$;

REVOKE ALL ON FUNCTION public.gravi_experiment_search(text, text, integer) FROM PUBLIC;
-- Supabase grants EXECUTE on public functions to anon by default; the search RPC must
-- not be callable unauthenticated.
REVOKE ALL ON FUNCTION public.gravi_experiment_search(text, text, integer) FROM anon;
GRANT EXECUTE ON FUNCTION public.gravi_experiment_search(text, text, integer) TO authenticated;
GRANT EXECUTE ON FUNCTION public.gravi_experiment_search(text, text, integer) TO bloom_user;
GRANT EXECUTE ON FUNCTION public.gravi_experiment_search(text, text, integer) TO bloom_admin;
GRANT EXECUTE ON FUNCTION public.gravi_experiment_search(text, text, integer) TO bloom_agent;

COMMIT;
