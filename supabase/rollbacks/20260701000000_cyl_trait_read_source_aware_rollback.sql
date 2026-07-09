-- Manual rollback for 20260701000000_cyl_trait_read_source_aware.sql
--
-- Drops the two new views and the source-aware get_scan_traits, and restores the prior
-- read surface: the legacy 2-arg get_scan_traits (LANGUAGE SQL, from 20241119232238) and
-- cyl_scan_trait_names = SELECT name FROM cyl_traits. Dependents are dropped before the
-- substrate view, and the 4-arg function is dropped BEFORE recreating the 2-arg one so no
-- overload ambiguity (PGRST203) is reintroduced.

BEGIN;

DROP VIEW IF EXISTS public.cyl_scan_trait_names;
DROP VIEW IF EXISTS public.cyl_scan_traits_latest;
DROP VIEW IF EXISTS public.cyl_scan_traits_source;

DROP FUNCTION IF EXISTS public.get_scan_traits(bigint, text, bigint, text);

-- Restore the prior 2-arg function verbatim (LANGUAGE SQL; its ORDER BY references the
-- SELECT-list output aliases, so the AS aliases must be preserved).
CREATE OR REPLACE FUNCTION get_scan_traits(experiment_id_ BIGINT, trait_name_ TEXT) RETURNS TABLE (
    scan_id BIGINT,
    date_scanned TEXT,
    plant_age_days INT,
    wave_number INT,
    plant_id BIGINT,
    germ_day INT,
    plant_qr_code TEXT,
    accession_name TEXT,
    trait_name TEXT,
    trait_value FLOAT
)
AS $$
    SELECT
    cyl_scans.id as scan_id,
    cyl_scans.date_scanned as date_scanned,
    cyl_scans.plant_age_days as plant_age_days,
    cyl_waves.number as wave_number,
    cyl_plants.id as plant_id,
    cyl_plants.germ_day as germ_day,
    cyl_plants.qr_code as plant_qr_code,
    accessions.name as accession_name,
    cyl_traits.name as trait_name,
    cyl_scan_traits.value as trait_value
    FROM
    species
    JOIN cyl_experiments on cyl_experiments.species_id = species.id
    JOIN cyl_waves on cyl_waves.experiment_id = cyl_experiments.id
    JOIN cyl_plants on cyl_plants.wave_id = cyl_waves.id
    JOIN accessions on cyl_plants.accession_id = accessions.id
    JOIN cyl_scans on cyl_scans.plant_id = cyl_plants.id
    JOIN cyl_scan_traits on cyl_scan_traits.scan_id = cyl_scans.id
    JOIN cyl_traits on cyl_scan_traits.trait_id = cyl_traits.id
    WHERE
    cyl_experiments.id = experiment_id_ AND
    cyl_traits.name = trait_name_
    ORDER BY
    accession_name, plant_id;
$$ LANGUAGE SQL;

-- Restore the prior registry-passthrough view + its read grant.
CREATE VIEW public.cyl_scan_trait_names AS
SELECT name FROM public.cyl_traits;

GRANT SELECT ON public.cyl_scan_trait_names
    TO bloom_agent, bloom_user, bloom_admin, authenticated;

COMMIT;
