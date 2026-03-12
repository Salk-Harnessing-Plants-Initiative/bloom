DROP FUNCTION IF EXISTS insert_image_v2_0;

CREATE OR REPLACE FUNCTION insert_image_v2_0(species_common_name TEXT, experiment TEXT, wave_number INT, germ_day INT, germ_day_color TEXT, plant_age_days INT, date_scanned_ DATE, device_name TEXT, plant_qr_code TEXT, accession_name TEXT, frame_number_ INT, phenotyper_name TEXT, phenotyper_email TEXT, scientist_name TEXT, scientist_email TEXT) RETURNS BIGINT AS $$
DECLARE
    species_id_var BIGINT;
    experiment_id_var BIGINT;
    wave_id_var BIGINT;
    accession_id_var BIGINT;
    plant_id_var BIGINT;
    scanner_id_var BIGINT;
    scan_id_var BIGINT;
    image_id_var BIGINT;
    status_var TEXT;
    phenotyper_id_var BIGINT;
    scientist_id_var BIGINT;

BEGIN
    -- Verify that the species exists
    SELECT id INTO species_id_var FROM species WHERE common_name = species_common_name;
    IF species_id_var IS NULL THEN
        RAISE EXCEPTION 'Species % does not exist', species_common_name;
    END IF;

    -- Verify that the scanner exists
    SELECT id INTO scanner_id_var FROM cyl_scanners WHERE name = device_name;
    IF scanner_id_var IS NULL THEN
        RAISE EXCEPTION 'Scanner % does not exist', device_name;
    END IF;

    -- Upsert the experiment
    INSERT INTO cyl_experiments (species_id, name) VALUES (species_id_var, experiment)
    ON CONFLICT (species_id, name) DO NOTHING RETURNING id INTO experiment_id_var;

    -- If experiment was not inserted, then retrieve its id
    IF experiment_id_var IS NULL THEN
        SELECT id INTO experiment_id_var FROM cyl_experiments WHERE species_id = species_id_var AND name = experiment;
    END IF;

    -- Upsert the wave
    INSERT INTO cyl_waves (experiment_id, number) VALUES (experiment_id_var, wave_number)
    ON CONFLICT (experiment_id, number) DO NOTHING RETURNING id INTO wave_id_var;

    -- If wave was not inserted, then retrieve its id
    IF wave_id_var IS NULL THEN
        SELECT id INTO wave_id_var FROM cyl_waves WHERE experiment_id = experiment_id_var AND number = wave_number;
    END IF;

    -- Upsert the accession
    INSERT INTO accessions (name) VALUES (accession_name)
    ON CONFLICT (name) DO NOTHING RETURNING id INTO accession_id_var;

    -- If accession was not inserted, then retrieve its id
    IF accession_id_var IS NULL THEN
        SELECT id INTO accession_id_var FROM accessions WHERE name = accession_name;
    END IF;

    -- Upsert the plant
    INSERT INTO cyl_plants (wave_id, qr_code, germ_day, germ_day_color, accession_id) VALUES (wave_id_var, plant_qr_code, germ_day, germ_day_color, accession_id_var)
    ON CONFLICT (wave_id, qr_code) DO NOTHING RETURNING id INTO plant_id_var;

    -- If plant was not inserted, then retrieve its id
    IF plant_id_var IS NULL THEN
        SELECT id INTO plant_id_var FROM cyl_plants WHERE wave_id = wave_id_var AND qr_code = plant_qr_code;
    END IF;

    -- Upsert the phenotyper
    INSERT INTO phenotypers (first_name, email)
    VALUES (phenotyper_name, email)
    ON CONFLICT (email) DO NOTHING
    RETURNING id INTO phenotyper_id_var;

    -- If not inserted, fetch the existing one
    IF phenotyper_id_var IS NULL THEN
    SELECT id INTO phenotyper_id_var FROM phenotypers WHERE email = phenotyper_email;
    END IF;

    -- Upsert the scientist
    INSERT INTO cyl_scientists (scientist_email, email)
    VALUES (scientist_email, email)
    ON CONFLICT (email) DO NOTHING
    RETURNING id INTO scientist_id_var;

    -- If not inserted, fetch the existing one
    IF scientist_id_var IS NULL THEN
    SELECT id INTO scientist_id_var FROM cyl_scientists WHERE email = scientist_email;
    END IF;

    -- Upsert the scan
    INSERT INTO cyl_scans (plant_id, scanner_id, phenotyper_id, scientist_id,  date_scanned, plant_age_days) VALUES (plant_id_var, scanner_id_var, phenotyper_id, scientist_id, date_scanned_, plant_age_days)
    ON CONFLICT (plant_id, date_scanned) DO NOTHING RETURNING id INTO scan_id_var;

    -- If scan was not inserted, then retrieve its id
    IF scan_id_var IS NULL THEN
        SELECT id INTO scan_id_var FROM cyl_scans WHERE plant_id = plant_id_var AND cyl_scans.date_scanned = date_scanned_;
    END IF;

    -- Upsert the image
    INSERT INTO cyl_images (scan_id, frame_number, status) VALUES (scan_id_var, frame_number_, 'PENDING')
    ON CONFLICT (scan_id, frame_number) DO NOTHING RETURNING id INTO image_id_var;

    -- If image was not inserted, then retrieve its id
    IF image_id_var IS NULL THEN
        SELECT id INTO image_id_var FROM cyl_images WHERE scan_id = scan_id_var AND frame_number = frame_number_;
    END IF;

    -- If the image status is SUCCESS, then return null
    SELECT status INTO status_var FROM cyl_images WHERE id = image_id_var;
    IF status_var = 'SUCCESS' THEN
        RETURN NULL;
    END IF;

    -- Return the image id if it was inserted or was pending
    RETURN image_id_var;

END;
$$ LANGUAGE plpgsql;
