-- bloom#637 / bloom#656 (supersedes PR #654's D7): rewrite get_experiment_summary_counts's
-- unpinned path as a live EXISTS semi-join (n_plants) + an on-demand-refresh cache read
-- (n_traits), per @blm3886's bloom#656 measurements -- n_plants via COUNT(DISTINCT ...) cost
-- 16.5s for one experiment (12.9s of that just dragging 13.8M rows through the join before
-- deduping); the EXISTS rewrite costs 247ms for ALL experiments and needs no cache.
--
-- The source_id_/run_id_-pinned branches keep a live join via a helper scoped to just that case
-- (simpler than PR #654's version, which also had to serve the unpinned path) -- with the same
-- two incidental, semantics-preserving cleanups Benfica's comment noted: `JOIN accessions` ->
-- `accession_id IS NOT NULL`, and the unnecessary `cyl_experiments` join dropped.
--
-- Manual rollback: supabase/rollbacks/20260817150000_rewrite_get_experiment_summary_counts_rollback.sql

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

-- Supabase auto-grants EXECUTE on new public-schema functions to anon/authenticated/service_role,
-- so PUBLIC alone doesn't close that (same lesson 20260817140000 already applies to
-- refresh_cyl_experiment_trait_counts) -- and it matters more here: this function is
-- SECURITY DEFINER, so an anon caller invoking it directly would run with the definer's elevated
-- privilege, bypassing whatever table-level grants anon itself lacks. Verified empirically
-- (SET LOCAL ROLE anon; SELECT * FROM compute_cyl_experiment_summary_counts_live(...) succeeded
-- before this fix) that anon could otherwise call it and read real data.
REVOKE EXECUTE ON FUNCTION public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text)
    FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

-- DROP FUNCTION first, not CREATE OR REPLACE alone -- this revision adds a column to the return
-- shape, and Postgres refuses to CREATE OR REPLACE a function across a return-type change ("cannot
-- change return type of existing function"). This makes the migration idempotently re-runnable
-- regardless of whether the previously-installed version is bloom#625's original 3-column shape
-- or an earlier run of this same 4-column one -- found when this migration's own idempotency
-- coverage (test_migration_body_is_idempotent, test_rewrite_rollback_restores_prior_body) started
-- failing against a local dev DB that had the 3-column shape installed first.
DROP FUNCTION IF EXISTS public.get_experiment_summary_counts(bigint, bigint, text);
CREATE FUNCTION public.get_experiment_summary_counts(
    experiment_id_ bigint DEFAULT NULL,
    source_id_     bigint DEFAULT NULL,
    run_id_        text   DEFAULT NULL
) RETURNS TABLE (
    experiment_id       bigint,
    n_plants            int,
    n_traits            int,
    -- Surfaces n_traits's own staleness (design.md D5) -- raised in round 4's review, then again
    -- by an external review in round 6, without either round actually closing it; closed here.
    -- NULL for a pinned (source_id_/run_id_) call, which is always live and has no cache to be
    -- stale against; otherwise cyl_experiment_trait_counts.updated_at, or NULL if this
    -- experiment's cache row has never been populated at all (no refresh has run yet, or it has
    -- zero matching traits -- see D2's "absent means zero" convention, same reasoning applies to
    -- an absent timestamp).
    n_traits_updated_at timestamptz
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
-- Not exploitable today (every reference in this body is already schema-qualified, and SECURITY
-- INVOKER carries no privilege-escalation vector regardless) -- pinned anyway for consistency with
-- every other function this change adds/rewrites, and because Supabase's security linter flags an
-- unpinned search_path on any function as "Function Search Path Mutable" regardless of exploitability.
-- Found in round-4 review; this function (and its bloom#625 predecessor) had been the one exception.
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF source_id_ IS NOT NULL AND run_id_ IS NOT NULL THEN
        RAISE EXCEPTION 'get_experiment_summary_counts: specify at most one of source_id_ and run_id_';
    END IF;

    IF source_id_ IS NULL AND run_id_ IS NULL THEN
        -- "Current latest" case: n_plants live (cheap semi-join), n_traits from a cache
        -- refreshed on demand only (design.md D8) -- unbounded staleness until someone
        -- dispatches a refresh; see design.md D5's caveat.
        RETURN QUERY
        SELECT p.experiment_id, p.n_plants, COALESCE(c.n_traits, 0)::int, c.updated_at
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
    -- arbitrary historical pin -- delegate to the shared live helper. Always live, so there's no
    -- cache staleness to report -- NULL, not the current time (which would misleadingly imply a
    -- refresh just happened).
    RETURN QUERY
    SELECT c.experiment_id, c.n_plants, c.n_traits, NULL::timestamptz
    FROM public.compute_cyl_experiment_summary_counts_live(experiment_id_, source_id_, run_id_) c;
END;
$$;

-- REVOKE also from anon (not just PUBLIC) -- closing the same Supabase-auto-grant gap fixed above
-- for compute_cyl_experiment_summary_counts_live; this function's own prior definition
-- (20260807000000_get_experiment_summary_counts.sql) never closed it either, so this also fixes a
-- pre-existing leak on the function this migration is already re-touching, not just new surface.
REVOKE EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text)
    FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

COMMIT;
