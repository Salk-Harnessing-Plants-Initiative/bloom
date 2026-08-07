-- bloommcp N+1 fix (bloom#625): aggregate per-experiment summary counts in one round trip.
--
-- list_experiments() (bloommcp/src/bloom_mcp/data_access/supabase_reader.py) currently calls
-- get_experiment_traits once PER experiment (224 calls on staging today) just to compute two
-- distinct counts client-side, then discards the fetched rows -- one experiment alone
-- (experiment_id=1) has 13.8M cyl_scan_traits rows fetched-and-discarded this way, which is
-- what turns into the observed multi-minute hang.
--
-- This adds a single aggregate function computing both counts server-side:
--
--   get_experiment_summary_counts(experiment_id_ DEFAULT NULL, source_id_ DEFAULT NULL,
--                                  run_id_ DEFAULT NULL)
--       -> (experiment_id, n_plants, n_traits) rows, one per experiment that has at least one
--          matching trait row under the given source/run selection (an experiment with none is
--          absent, not zero-valued -- bloommcp's list_experiments() already holds the full
--          experiment-id list from its own cheap SELECT and defaults missing entries to zero).
--
-- Reuses get_experiment_traits's exact join chain and latest/source_id/run_id disjunction
-- against cyl_scan_traits_source (including the accessions join -- NOT dead weight here, unlike
-- the species join get_experiment_traits itself dropped; see design.md D2/Risks for why dropping
-- it would silently make this function MORE inclusive than get_experiment_traits, the opposite
-- of this change's "match load_experiment's semantics" goal). Unlike get_experiment_traits,
-- experiment_id_ also defaults to NULL here so a single unpinned call can return every
-- experiment's counts at once -- the shape bloommcp's list_experiments() needs.
--
-- EXECUTE is explicitly granted to the four read roles (not left on an implicit PUBLIC default),
-- matching get_experiment_traits's own posture.
--
-- Manual rollback: supabase/rollbacks/20260807000000_get_experiment_summary_counts_rollback.sql

BEGIN;

-- CREATE OR REPLACE (not bare CREATE) so the migration body is safely re-runnable.
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

    RETURN QUERY
    SELECT
        cyl_experiments.id AS experiment_id,
        COUNT(DISTINCT cyl_plants.id)::int AS n_plants,
        COUNT(DISTINCT src.trait_name)::int AS n_traits
    FROM cyl_experiments
    JOIN cyl_waves       ON cyl_waves.experiment_id = cyl_experiments.id
    JOIN cyl_plants      ON cyl_plants.wave_id = cyl_waves.id
    JOIN accessions      ON cyl_plants.accession_id = accessions.id
    JOIN cyl_scans       ON cyl_scans.plant_id = cyl_plants.id
    JOIN public.cyl_scan_traits_source src ON src.scan_id = cyl_scans.id
    WHERE (experiment_id_ IS NULL OR cyl_experiments.id = experiment_id_)
      AND (
            (source_id_ IS NULL AND run_id_ IS NULL AND src.is_latest)
         OR (source_id_ IS NOT NULL AND src.source_id = source_id_)
         OR (run_id_ IS NOT NULL AND src.source_id = (
                SELECT max(s2.source_id)
                FROM public.cyl_scan_traits_source s2
                WHERE s2.scan_id = src.scan_id
                  AND s2.trait_id = src.trait_id
                  AND s2.pipeline_run_id = run_id_))
          )
    GROUP BY cyl_experiments.id;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_experiment_summary_counts(bigint, bigint, text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

COMMIT;
