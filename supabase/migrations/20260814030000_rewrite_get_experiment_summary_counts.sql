-- bloom#637 / bloom#656 (supersedes PR #654's D7): rewrite get_experiment_summary_counts's
-- unpinned path as a live EXISTS semi-join (n_plants) + a scheduled-refresh cache read
-- (n_traits), per @blm3886's bloom#656 measurements -- n_plants via COUNT(DISTINCT ...) cost
-- 16.5s for one experiment (12.9s of that just dragging 13.8M rows through the join before
-- deduping); the EXISTS rewrite costs 247ms for ALL experiments and needs no cache.
--
-- The source_id_/run_id_-pinned branches keep a live join via a helper scoped to just that case
-- (simpler than PR #654's version, which also had to serve the unpinned path) -- with the same
-- two incidental, semantics-preserving cleanups Benfica's comment noted: `JOIN accessions` ->
-- `accession_id IS NOT NULL`, and the unnecessary `cyl_experiments` join dropped.
--
-- Manual rollback: supabase/rollbacks/20260814030000_rewrite_get_experiment_summary_counts_rollback.sql

BEGIN;

CREATE OR REPLACE FUNCTION public.compute_cyl_experiment_summary_counts_live(
    experiment_id_ bigint,
    source_id_     bigint,
    run_id_        text
) RETURNS TABLE (
    experiment_id bigint, n_plants int, n_traits int
) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RETURN QUERY
    WITH matched AS (
        SELECT w.experiment_id, p.id AS plant_id, src.trait_name
        FROM public.cyl_waves       w
        JOIN public.cyl_plants      p   ON p.wave_id = w.id AND p.accession_id IS NOT NULL
        JOIN public.cyl_scans       s   ON s.plant_id = p.id
        JOIN public.cyl_scan_traits_source src ON src.scan_id = s.id
        WHERE (experiment_id_ IS NULL OR w.experiment_id = experiment_id_)
          AND (
                (source_id_ IS NOT NULL AND src.source_id = source_id_)
             OR (run_id_ IS NOT NULL AND src.source_id = (
                    SELECT max(s2.source_id) FROM public.cyl_scan_traits_source s2
                    WHERE s2.scan_id = src.scan_id AND s2.trait_id = src.trait_id
                      AND s2.pipeline_run_id = run_id_))
              )
    ),
    plant_counts AS (
        SELECT d.experiment_id, count(*)::int AS n_plants
        FROM (SELECT DISTINCT matched.experiment_id, matched.plant_id FROM matched) d
        GROUP BY d.experiment_id
    ),
    trait_counts AS (
        SELECT d.experiment_id, count(*)::int AS n_traits
        FROM (
            SELECT DISTINCT matched.experiment_id, matched.trait_name
            FROM matched WHERE matched.trait_name IS NOT NULL
        ) d
        GROUP BY d.experiment_id
    )
    SELECT p.experiment_id, p.n_plants, COALESCE(t.n_traits, 0)::int
    FROM plant_counts p LEFT JOIN trait_counts t ON t.experiment_id = p.experiment_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

CREATE OR REPLACE FUNCTION public.get_experiment_summary_counts(
    experiment_id_ bigint DEFAULT NULL,
    source_id_     bigint DEFAULT NULL,
    run_id_        text   DEFAULT NULL
) RETURNS TABLE (
    experiment_id bigint,
    n_plants      int,
    n_traits      int
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
AS $$
BEGIN
    IF source_id_ IS NOT NULL AND run_id_ IS NOT NULL THEN
        RAISE EXCEPTION 'get_experiment_summary_counts: specify at most one of source_id_ and run_id_';
    END IF;

    IF source_id_ IS NULL AND run_id_ IS NULL THEN
        -- "Current latest" case: n_plants live (cheap semi-join), n_traits from the
        -- scheduled-refresh cache (may lag up to one refresh interval -- design.md D5).
        RETURN QUERY
        SELECT p.experiment_id, p.n_plants, COALESCE(c.n_traits, 0)::int
        FROM (
            SELECT w.experiment_id, count(DISTINCT p.id)::int AS n_plants
            FROM public.cyl_waves  w
            JOIN public.cyl_plants p ON p.wave_id = w.id AND p.accession_id IS NOT NULL
            JOIN public.cyl_scans  s ON s.plant_id = p.id
            WHERE (experiment_id_ IS NULL OR w.experiment_id = experiment_id_)
              AND EXISTS (SELECT 1 FROM public.cyl_scan_traits t WHERE t.scan_id = s.id)
            GROUP BY w.experiment_id
        ) p
        LEFT JOIN public.cyl_experiment_trait_counts c ON c.experiment_id = p.experiment_id;
        RETURN;
    END IF;

    -- source_id_/run_id_ pin: neither the live semi-join nor the n_traits cache covers an
    -- arbitrary historical pin -- delegate to the shared live helper.
    RETURN QUERY
    SELECT * FROM public.compute_cyl_experiment_summary_counts_live(experiment_id_, source_id_, run_id_);
END;
$$;

REVOKE EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

COMMIT;
