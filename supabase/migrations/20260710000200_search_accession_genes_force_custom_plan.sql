-- Migration: search_accession_genes_force_custom_plan
--
-- search_accession_genes autocompletes over the 12.4M-row accession proteins.
-- A common prefix (e.g. 'Ath_100') matches millions of rows. Called by PostgREST
-- as a parameterized statement, the plan goes GENERIC after ~5 executions and
-- locks to "trgm Bitmap -> Sort": it materialises every match and sorts by uid
-- before LIMIT 20 -> seconds -> API statement_timeout (HTTP 500). A CUSTOM plan
-- for the same term instead walks the uid index and stops at LIMIT (sub-ms).
--
-- Fix: make the function plpgsql with plan_cache_mode = force_custom_plan so its
-- internal query is re-planned for the actual search term on every call — the
-- optimal plan per input. Behaviour, signature, and result shape are unchanged.
--
-- Additive: only replaces the function body/language; grants are preserved by
-- CREATE OR REPLACE and re-granted below for safety.

BEGIN;

CREATE OR REPLACE FUNCTION public.search_accession_genes(
  partial_id  text,
  max_results int DEFAULT 20
)
RETURNS TABLE (
  uid            text,
  accession_id   bigint,
  accession_name text,
  gene_id        text
)
LANGUAGE plpgsql STABLE
SET plan_cache_mode = 'force_custom_plan'
AS $$
BEGIN
  -- Empty / whitespace-only / NULL partial_id returns zero rows. Scoped to
  -- accession proteins only (accession_id IS NOT NULL). Hard cap at 1000.
  RETURN QUERY
    SELECT p.uid, p.accession_id, a.common_name AS accession_name, p.gene_id
      FROM public.proteins p
      JOIN public.arabidopsis_accessions a ON a.id = p.accession_id
     WHERE p.accession_id IS NOT NULL
       AND char_length(btrim(partial_id)) >= 1
       AND ( p.uid     ILIKE '%' || partial_id || '%'
          OR p.gene_id ILIKE '%' || partial_id || '%' )
     ORDER BY p.uid
     LIMIT GREATEST(1, LEAST(max_results, 1000));
END;
$$;

COMMENT ON FUNCTION public.search_accession_genes(text, int) IS
  'Case-insensitive substring match on accession proteins (accession_id IS NOT NULL) by uid or gene_id, joined to the accession name. plpgsql + force_custom_plan so a common prefix over millions of rows re-plans to the uid-index walk instead of a generic Bitmap->Sort. Empty/whitespace/NULL returns zero rows; max_results hard-capped at 1000.';

GRANT EXECUTE ON FUNCTION public.search_accession_genes(text, int)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
