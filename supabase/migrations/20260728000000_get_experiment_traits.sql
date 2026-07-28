-- bloommcp data-access roadmap Tier 1 (bloom#546): bulk trait reads for one experiment.
--
-- get_scan_traits is per-trait: loading one experiment means one call per distinct trait
-- name (649-880 round trips for the bloom#483 cylinder fixture). This adds a bulk sibling:
--
--   get_experiment_traits(...)         same latest/source_id/run_id semantics as
--                                       get_scan_traits, minus the trait_name_ filter -- every
--                                       trait for the experiment in one round trip.
--   list_experiment_trait_sources(...) distinct real (non-NULL) sources contributing to the
--                                       experiment, so a caller can enumerate sources/runs
--                                       before pinning one.
--
-- Additive/forward-only: creates two new functions, touches no existing table, view, or
-- function (get_scan_traits, cyl_scan_traits_source/_latest, cyl_scan_trait_names are all
-- unchanged). Both are SECURITY INVOKER, matching get_scan_traits's posture, and reuse
-- cyl_scan_traits_source's is_latest selection rule rather than re-deriving it.
--
-- RPC shape (bulk RPC vs. a PostgREST embedded-join query) is Decision D1 in this change's
-- design.md -- gated on @blm3886 (Benfica)'s review per bloom#546; do not merge unreviewed.
--
-- Manual rollback: supabase/rollbacks/20260728000000_get_experiment_traits_rollback.sql

BEGIN;

-- 1. Bulk long-format read: every trait for one experiment in a single round trip.
-- CREATE OR REPLACE (not bare CREATE) so the migration body is safely re-runnable.
CREATE OR REPLACE FUNCTION public.get_experiment_traits(
    experiment_id_ bigint,
    source_id_     bigint DEFAULT NULL,
    run_id_        text   DEFAULT NULL
) RETURNS TABLE (
    scan_id        bigint,
    date_scanned   text,
    plant_age_days int,
    wave_number    int,
    plant_id       bigint,
    germ_day       int,
    plant_qr_code  text,
    accession_name text,
    trait_name     text,
    source_id      bigint,
    trait_value    float
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
AS $$
BEGIN
    IF source_id_ IS NOT NULL AND run_id_ IS NOT NULL THEN
        RAISE EXCEPTION 'get_experiment_traits: specify at most one of source_id_ and run_id_';
    END IF;

    RETURN QUERY
    SELECT
        cyl_scans.id::bigint,
        cyl_scans.date_scanned::text,
        cyl_scans.plant_age_days::int,
        cyl_waves.number::int,
        cyl_plants.id::bigint,
        cyl_plants.germ_day::int,
        cyl_plants.qr_code::text,
        accessions.name::text,
        src.trait_name::text,
        src.source_id::bigint,
        src.value::float
    FROM species
    JOIN cyl_experiments ON cyl_experiments.species_id = species.id
    JOIN cyl_waves       ON cyl_waves.experiment_id = cyl_experiments.id
    JOIN cyl_plants      ON cyl_plants.wave_id = cyl_waves.id
    JOIN accessions      ON cyl_plants.accession_id = accessions.id
    JOIN cyl_scans       ON cyl_scans.plant_id = cyl_plants.id
    JOIN public.cyl_scan_traits_source src ON src.scan_id = cyl_scans.id
    WHERE cyl_experiments.id = experiment_id_
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
    ORDER BY accessions.name, cyl_plants.id, src.trait_name;
END;
$$;

-- 2. Source/run discovery: enumerate an experiment's real sources before pinning one.
CREATE OR REPLACE FUNCTION public.list_experiment_trait_sources(
    experiment_id_ bigint
) RETURNS TABLE (
    source_id       bigint,
    source_name     text,
    pipeline_run_id text
)
LANGUAGE sql
STABLE
SECURITY INVOKER
AS $$
    SELECT DISTINCT src.source_id, src.source_name, src.pipeline_run_id
    FROM cyl_experiments
    JOIN cyl_waves  ON cyl_waves.experiment_id = cyl_experiments.id
    JOIN cyl_plants ON cyl_plants.wave_id = cyl_waves.id
    JOIN cyl_scans  ON cyl_scans.plant_id = cyl_plants.id
    JOIN public.cyl_scan_traits_source src ON src.scan_id = cyl_scans.id
    WHERE cyl_experiments.id = experiment_id_
      AND src.source_id IS NOT NULL;
$$;

COMMIT;
