-- Migration: search_accession_genes_accession_filter
--
-- Adds an optional filter_accession_id argument to search_accession_genes so the
-- "Find similar proteins" gene picker can scope its type-ahead to the accession
-- the user selected. A Helixer gene_id exists in exactly one accession, so an
-- unscoped search lets the user pick a gene that isn't in the chosen accession
-- and dead-end on "no variant". Scoping the suggestions makes that impossible.
--
-- filter_accession_id defaults to NULL (search every accession), so existing
-- 2-argument callers are unaffected. Keeps the plpgsql + force_custom_plan shape
-- from 20260710000200 (a common prefix over millions of rows must re-plan to the
-- uid-index walk, not a generic Bitmap->Sort).
--
-- Signature changes (adds a third argument) so the 2-arg function is dropped and
-- replaced; 2-arg calls resolve to the new function via the defaults.

BEGIN;

DROP FUNCTION IF EXISTS public.search_accession_genes(text, int);

CREATE OR REPLACE FUNCTION public.search_accession_genes(
  partial_id          text,
  max_results         int    DEFAULT 20,
  filter_accession_id bigint DEFAULT NULL
)
RETURNS TABLE (
  uid            text,
  accession_id   bigint,
  accession_name text,
  gene_id        text
)
LANGUAGE plpgsql STABLE
SET plan_cache_mode = 'force_custom_plan'
AS $fn$
BEGIN
  -- A non-empty partial_id filters by substring. An EMPTY partial_id lists the
  -- scoped accession's genes (so users can browse the ID format without knowing
  -- it) when filter_accession_id is set; with no filter, empty returns zero rows
  -- (never lists all accessions). Scoped to accession proteins. Hard cap 1000.
  RETURN QUERY
    SELECT p.uid, p.accession_id, a.common_name AS accession_name, p.gene_id
      FROM public.proteins p
      JOIN public.arabidopsis_accessions a ON a.id = p.accession_id
     WHERE p.accession_id IS NOT NULL
       AND (filter_accession_id IS NULL OR p.accession_id = filter_accession_id)
       AND CASE
             WHEN char_length(btrim(coalesce(partial_id, ''))) >= 1
               THEN ( p.uid     ILIKE '%' || partial_id || '%'
                   OR p.gene_id ILIKE '%' || partial_id || '%' )
             ELSE filter_accession_id IS NOT NULL
           END
     ORDER BY p.uid
     LIMIT GREATEST(1, LEAST(max_results, 1000));
END;
$fn$;

COMMENT ON FUNCTION public.search_accession_genes(text, int, bigint) IS
  'Case-insensitive substring match on accession proteins (accession_id IS NOT NULL) by uid or gene_id, joined to the accession name. filter_accession_id scopes suggestions to one accession (NULL = all). An empty partial_id lists the scoped accession''s genes (browse without knowing the ID format); empty with no filter returns zero rows. plpgsql + force_custom_plan so a common prefix over millions of rows re-plans to the uid-index walk instead of a generic Bitmap->Sort. max_results hard-capped at 1000.';

GRANT EXECUTE ON FUNCTION public.search_accession_genes(text, int, bigint)
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
