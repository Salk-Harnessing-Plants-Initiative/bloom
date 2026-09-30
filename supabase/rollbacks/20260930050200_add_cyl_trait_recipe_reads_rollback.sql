-- Rollback for 20260930050200_add_cyl_trait_recipe_reads.sql.
-- Change: add-cyl-trait-recipe-key (bloom#935).
--
-- This is the STAGING HOT-APPLY only. Applying it by hand leaves 20260930050200
-- recorded as applied, so CI, fresh stacks and the next promotion would still apply
-- it. A durable rollback is a new forward migration whose body is this file. After a
-- hand-apply, run
--   supabase migration repair --status reverted 20260930050200
--
-- ORDER: apply the dataset recipe-mode rollback (20260930050300) first; its
-- create_cyl_dataset calls _cyl_trait_recipe_presence, which this drops.
--
-- Drops the recipe read functions and restores 20260728000000's three-argument
-- get_experiment_traits verbatim, with its grants. anon regains EXECUTE through
-- Supabase default privileges, as before.

BEGIN;

DROP FUNCTION IF EXISTS public.get_trait_recipe_coverage(bigint[], bigint[], text);
DROP FUNCTION IF EXISTS public.list_trait_recipes(bigint[], bigint[]);
DROP FUNCTION IF EXISTS public.get_experiment_traits(bigint, bigint, text, text, bigint[]);
DROP FUNCTION IF EXISTS public._cyl_trait_recipe_presence(bigint[], bigint[]);

-- Copied verbatim from 20260728000000_get_experiment_traits.sql.
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
    -- No species join: no species column is ever selected here, and cyl_experiments.species_id
    -- is nullable -- an inner join through species would silently return zero rows for any
    -- experiment with a NULL species_id, disagreeing with list_experiment_trait_sources (below),
    -- which has no such join. Start from cyl_experiments directly instead.
    FROM cyl_experiments
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
    -- cyl_scans.id (table-qualified, not bare scan_id -- ambiguous against the OUT column
    -- under variable_conflict = error) breaks ties between multiple scans of the same plant;
    -- without it the order among such rows is unstable across calls.
    ORDER BY accessions.name, cyl_plants.id, cyl_scans.id, src.trait_name;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.get_experiment_traits(bigint, bigint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.get_experiment_traits(bigint, bigint, text)
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

ALTER FUNCTION public.get_experiment_traits(bigint, bigint, text) OWNER TO postgres;

NOTIFY pgrst, 'reload schema';

COMMIT;
