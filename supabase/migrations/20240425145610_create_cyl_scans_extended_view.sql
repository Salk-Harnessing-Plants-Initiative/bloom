-- convenience view for getting all scan data in one place

CREATE VIEW cyl_scans_extended AS
    SELECT
        cyl_scans.id as scan_id,
        cyl_scans.date_scanned as date_scanned,
        cyl_scans.scanner_id as scanner_id,
        cyl_scans.phenotyper_id as phenotyper_id,
        cyl_scans.plant_age_days as plant_age_days,
        cyl_scans.uploaded_at as uploaded_at,
        cyl_plants.id as plant_id,
        cyl_plants.qr_code as qr_code,
        cyl_plants.accession_id as accession_id,
        cyl_plants.germ_day as germ_day,
        cyl_plants.germ_day_color as germ_day_color,
        cyl_waves.id as wave_id,
        cyl_waves.number as wave_number,
        cyl_waves.name as wave_name,
        cyl_experiments.id as experiment_id,
        cyl_experiments.name as experiment_name,
        cyl_experiments.description as experiment_description,
        species.id as species_id,
        species.common_name as species_name,
        species.genus as species_genus,
        species.species as species_species
    FROM cyl_scans
    JOIN cyl_plants ON cyl_scans.plant_id = cyl_plants.id
    JOIN cyl_waves ON cyl_plants.wave_id = cyl_waves.id
    JOIN cyl_experiments ON cyl_waves.experiment_id = cyl_experiments.id
    JOIN species on cyl_experiments.species_id = species.id;