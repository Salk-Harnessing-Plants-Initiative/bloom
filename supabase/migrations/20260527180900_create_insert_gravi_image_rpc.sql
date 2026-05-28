-- insert_gravi_image: per-image upsert path called by the Bloom Desktop
-- GraviScan upload (once per image). Ported from V1 production.
--
-- Parameters match the bloom-js wrapper at @salk-hpi/bloom-js@0.3.0-dev.3.
-- The bloom-js param `plate_barcode_` maps to gravi_scans.plate_id (the V1
-- column rename is hidden inside the RPC body for client compatibility).
--
-- Behaviour:
--   1. Look up species; raise if missing
--   2. Upsert scanner / phenotyper / scientist / accession / experiment
--      (each protected by its own UNIQUE constraint, ON CONFLICT DO NOTHING).
--      These are called per-image but only do real work on the first call —
--      subsequent calls hit the conflict and no-op. Performance follow-up is
--      a separate concern (move reference upserts into the session RPC).
--   3. Pre-insert duplicate check on (experiment_id, plate_id, capture_date)
--      via idx_gravi_scans_natural_key; returns NULL on duplicate so the
--      caller knows to skip the binary upload.
--   4. INSERT into gravi_scans and return its id. The caller subsequently
--      INSERTs into gravi_images directly (not via this RPC).
--
-- EXECUTE is revoked from PUBLIC and granted only to the bloom roles that
-- legitimately need it.

DROP FUNCTION IF EXISTS insert_gravi_image;

CREATE OR REPLACE FUNCTION insert_gravi_image(
    species_common_name TEXT,
    experiment_name TEXT,
    scanner_name TEXT,
    phenotyper_name TEXT,
    phenotyper_email TEXT,
    scientist_name TEXT,
    scientist_email TEXT,
    plate_barcode_ TEXT,
    capture_date_ TIMESTAMPTZ,
    grid_mode_ TEXT,
    plate_index_ TEXT,
    resolution_ INT,
    format_ TEXT,
    accession_name TEXT DEFAULT NULL,
    cycle_number_ INT DEFAULT NULL,
    session_id_ BIGINT DEFAULT NULL,
    system_name_ TEXT DEFAULT NULL,
    metadata_id_ BIGINT DEFAULT NULL,
    wave_number_ INT DEFAULT NULL,
    transplant_date_ TIMESTAMPTZ DEFAULT NULL,
    custom_note_ TEXT DEFAULT NULL
) RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    species_id_var BIGINT;
    experiment_id_var BIGINT;
    scanner_id_var BIGINT;
    phenotyper_id_var BIGINT;
    scientist_id_var BIGINT;
    accession_id_var BIGINT;
    scan_id_var BIGINT;
BEGIN
    -- Verify that the species exists
    SELECT id INTO species_id_var FROM species WHERE common_name = species_common_name;
    IF species_id_var IS NULL THEN
        RAISE EXCEPTION 'Species % does not exist', species_common_name;
    END IF;

    -- Upsert the scanner
    INSERT INTO gravi_scanners (name) VALUES (scanner_name)
    ON CONFLICT (name) DO NOTHING RETURNING id INTO scanner_id_var;
    IF scanner_id_var IS NULL THEN
        SELECT id INTO scanner_id_var FROM gravi_scanners WHERE name = scanner_name;
    END IF;

    -- Upsert the phenotyper
    INSERT INTO phenotypers (first_name, email) VALUES (phenotyper_name, phenotyper_email)
    ON CONFLICT (email) DO NOTHING RETURNING id INTO phenotyper_id_var;
    IF phenotyper_id_var IS NULL THEN
        SELECT id INTO phenotyper_id_var FROM phenotypers WHERE email = phenotyper_email;
    END IF;

    -- Upsert the scientist
    INSERT INTO cyl_scientists (scientist_name, email) VALUES (scientist_name, scientist_email)
    ON CONFLICT (email) DO NOTHING RETURNING id INTO scientist_id_var;
    IF scientist_id_var IS NULL THEN
        SELECT id INTO scientist_id_var FROM cyl_scientists WHERE email = scientist_email;
    END IF;

    -- Upsert the accession (if provided)
    IF accession_name IS NOT NULL AND accession_name != '' THEN
        INSERT INTO accessions (name) VALUES (accession_name)
        ON CONFLICT (name) DO NOTHING RETURNING id INTO accession_id_var;
        IF accession_id_var IS NULL THEN
            SELECT id INTO accession_id_var FROM accessions WHERE name = accession_name;
        END IF;
    END IF;

    -- Upsert the experiment (unique per species + name + system)
    INSERT INTO gravi_experiments (species_id, name, scientist_id, accession_id, system_name)
    VALUES (species_id_var, experiment_name, scientist_id_var, accession_id_var, system_name_)
    ON CONFLICT (species_id, name, system_name) DO NOTHING RETURNING id INTO experiment_id_var;
    IF experiment_id_var IS NULL THEN
        SELECT id INTO experiment_id_var FROM gravi_experiments
        WHERE species_id = species_id_var
          AND name = experiment_name
          AND system_name IS NOT DISTINCT FROM system_name_;
    END IF;

    -- Pre-insert duplicate check on the natural key.
    SELECT id INTO scan_id_var FROM gravi_scans
    WHERE experiment_id = experiment_id_var
      AND plate_id = plate_barcode_
      AND capture_date = capture_date_;

    IF scan_id_var IS NOT NULL THEN
        -- Scan already exists, return NULL so caller skips the binary upload.
        RETURN NULL;
    END IF;

    -- No duplicate found, insert new scan.
    INSERT INTO gravi_scans (
        experiment_id, phenotyper_id, scanner_id,
        session_id, cycle_number,
        plate_id, capture_date,
        grid_mode, plate_index, resolution, format,
        metadata_id, wave_number, transplant_date, custom_note
    ) VALUES (
        experiment_id_var, phenotyper_id_var, scanner_id_var,
        session_id_, cycle_number_,
        plate_barcode_, capture_date_,
        grid_mode_, plate_index_, resolution_, format_,
        metadata_id_, wave_number_, transplant_date_, custom_note_
    ) RETURNING id INTO scan_id_var;

    RETURN scan_id_var;
END;
$$;

-- Scope EXECUTE: only the bloom roles that should be calling this RPC.
REVOKE EXECUTE ON FUNCTION insert_gravi_image FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insert_gravi_image TO bloom_user, bloom_writer, bloom_admin;
