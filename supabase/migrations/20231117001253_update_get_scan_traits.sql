-- update function get_scan_traits to return wave_number

DROP FUNCTION get_scan_traits(experiment_id_ BIGINT, plant_age_days_ INT, trait_name_ TEXT);

CREATE OR REPLACE FUNCTION get_scan_traits(experiment_id_ BIGINT, plant_age_days_ INT, trait_name_ TEXT) RETURNS TABLE (
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
    cyl_scan_traits.name as trait_name,
    cyl_scan_traits.value as trait_value
    FROM
    species
    JOIN cyl_experiments on cyl_experiments.species_id = species.id
    JOIN cyl_waves on cyl_waves.experiment_id = cyl_experiments.id
    JOIN cyl_plants on cyl_plants.wave_id = cyl_waves.id
    JOIN accessions on cyl_plants.accession_id = accessions.id
    JOIN cyl_scans on cyl_scans.plant_id = cyl_plants.id
    JOIN cyl_scan_traits on cyl_scan_traits.scan_id = cyl_scans.id
    WHERE
    cyl_experiments.id = experiment_id_ AND
    cyl_scans.plant_age_days = plant_age_days_ AND
    cyl_scan_traits.name = trait_name_
    ORDER BY
    accession_name, plant_id;
$$ LANGUAGE SQL;

-- create version of get_scan_traits that is not filtered by plant_age_days

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
    cyl_scan_traits.name as trait_name,
    cyl_scan_traits.value as trait_value
    FROM
    species
    JOIN cyl_experiments on cyl_experiments.species_id = species.id
    JOIN cyl_waves on cyl_waves.experiment_id = cyl_experiments.id
    JOIN cyl_plants on cyl_plants.wave_id = cyl_waves.id
    JOIN accessions on cyl_plants.accession_id = accessions.id
    JOIN cyl_scans on cyl_scans.plant_id = cyl_plants.id
    JOIN cyl_scan_traits on cyl_scan_traits.scan_id = cyl_scans.id
    WHERE
    cyl_experiments.id = experiment_id_ AND
    cyl_scan_traits.name = trait_name_
    ORDER BY
    accession_name, plant_id;
$$ LANGUAGE SQL;
