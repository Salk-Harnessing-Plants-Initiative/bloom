-- A2 read-path (bloom#298): make the cyl trait READ path source-aware.
--
-- The write-back RPC (#371) mints one cyl_trait_sources row per pipeline run, so a
-- reprocessed scan carries MULTIPLE sources and the source-blind get_scan_traits
-- returned duplicate/ambiguous rows. This adds a single shared read surface:
--
--   cyl_scan_traits_source  substrate view; one row per cyl_scan_traits row exposing
--                           the source dimension + is_latest = max(source_id) per scan
--                           (window aggregate; legacy NULL-source rows count as latest).
--   cyl_scan_traits_latest  the is_latest rows (canonical latest-per-scan surface).
--   get_scan_traits(...)    latest by default; pin a source_id; group by pipeline run
--                           (run_id_, "as of run X", deduped to the latest delivery within
--                           the run); both optional args set -> error. The legacy 2-arg
--                           function is dropped so exactly one candidate exists (no PostgREST
--                           overload ambiguity, PGRST203); 2-arg callers bind the defaults.
--   cyl_scan_trait_names    distinct non-null trait names present in latest data.
--
-- Additive/forward-only: creates/redefines read objects, touches no table or data. Reads
-- stay open to the existing read roles; no RLS or write-grant change. "Latest" = max(source_id)
-- because cyl_trait_sources has no timestamp and identity ids increase as reprocessing runs.
--
-- Manual rollback: supabase/rollbacks/20260701000000_cyl_trait_read_source_aware_rollback.sql

BEGIN;

-- 1. Substrate: the single source of truth for the latest-selection rule.
CREATE OR REPLACE VIEW public.cyl_scan_traits_source
WITH (security_invoker = on) AS
SELECT
    cst.scan_id,
    cst.trait_id,
    t.name                                AS trait_name,
    cst.value,
    cst.source_id,
    s.name                                AS source_name,
    s.metadata ->> 'pipeline_run_id'      AS pipeline_run_id,
    (cst.source_id IS NOT DISTINCT FROM
        max(cst.source_id) OVER (PARTITION BY cst.scan_id)) AS is_latest
FROM public.cyl_scan_traits cst
LEFT JOIN public.cyl_trait_sources s ON s.id = cst.source_id
LEFT JOIN public.cyl_traits       t ON t.id = cst.trait_id;

GRANT SELECT ON public.cyl_scan_traits_source
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

-- 2. Canonical latest-per-scan surface.
CREATE OR REPLACE VIEW public.cyl_scan_traits_latest
WITH (security_invoker = on) AS
SELECT scan_id, trait_id, trait_name, source_id, value
FROM public.cyl_scan_traits_source
WHERE is_latest;

GRANT SELECT ON public.cyl_scan_traits_latest
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

-- 3. Source-disambiguated trait-name listing (distinct non-null latest names). The DROP+CREATE
--    (not OR REPLACE) is required because the row content changes; re-issue the grant since the
--    prior object-level read access does not survive DROP VIEW.
DROP VIEW IF EXISTS public.cyl_scan_trait_names;
CREATE VIEW public.cyl_scan_trait_names
WITH (security_invoker = on) AS
SELECT DISTINCT trait_name AS name
FROM public.cyl_scan_traits_latest
WHERE trait_name IS NOT NULL
ORDER BY name;

GRANT SELECT ON public.cyl_scan_trait_names
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

-- 4. Source-aware get_scan_traits. Drop the legacy 2-arg function first so only one candidate
--    remains, then create the 4-arg form. plpgsql (needs RAISE for the both-args guard), STABLE,
--    SECURITY INVOKER. All select/sort identifiers are table-qualified so the RETURNS TABLE OUT
--    columns cannot collide with same-named table columns (e.g. plant_id) under variable_conflict.
DROP FUNCTION IF EXISTS public.get_scan_traits(bigint, text);

CREATE OR REPLACE FUNCTION public.get_scan_traits(
    experiment_id_ bigint,
    trait_name_    text,
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
    trait_value    float
)
LANGUAGE plpgsql
STABLE
SECURITY INVOKER
AS $$
BEGIN
    IF source_id_ IS NOT NULL AND run_id_ IS NOT NULL THEN
        RAISE EXCEPTION 'get_scan_traits: specify at most one of source_id_ and run_id_';
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
        src.value::float
    FROM species
    JOIN cyl_experiments ON cyl_experiments.species_id = species.id
    JOIN cyl_waves       ON cyl_waves.experiment_id = cyl_experiments.id
    JOIN cyl_plants      ON cyl_plants.wave_id = cyl_waves.id
    JOIN accessions      ON cyl_plants.accession_id = accessions.id
    JOIN cyl_scans       ON cyl_scans.plant_id = cyl_plants.id
    JOIN public.cyl_scan_traits_source src ON src.scan_id = cyl_scans.id
    WHERE cyl_experiments.id = experiment_id_
      AND src.trait_name = trait_name_
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
    ORDER BY accessions.name, cyl_plants.id;
END;
$$;

COMMIT;
