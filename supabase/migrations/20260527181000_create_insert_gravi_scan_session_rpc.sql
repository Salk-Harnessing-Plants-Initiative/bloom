-- insert_gravi_scan_session: per-session upsert path called once per upload
-- batch (before image uploads start). Ported from V1 production.
--
-- Same reference upsert pattern as insert_gravi_image (species → scanner →
-- phenotyper → scientist → accession → experiment) and then INSERTs a
-- gravi_scan_sessions row. Returns the new session id; the desktop maps
-- local session UUIDs to these Supabase ids and passes the mapped id when
-- calling insert_gravi_image per image.

DROP FUNCTION IF EXISTS insert_gravi_scan_session;

CREATE OR REPLACE FUNCTION insert_gravi_scan_session(
    species_common_name TEXT,
    experiment_name TEXT,
    phenotyper_name TEXT,
    phenotyper_email TEXT,
    scientist_name TEXT,
    scientist_email TEXT,
    accession_name TEXT DEFAULT NULL,
    scan_mode_ TEXT DEFAULT 'single',
    interval_seconds_ INT DEFAULT NULL,
    duration_seconds_ INT DEFAULT NULL,
    total_cycles_ INT DEFAULT NULL,
    actual_duration_seconds_ INT DEFAULT NULL,
    completed_at_ TIMESTAMPTZ DEFAULT NULL,
    cancelled_ BOOLEAN DEFAULT false,
    system_name_ TEXT DEFAULT NULL
) RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    species_id_var BIGINT;
    experiment_id_var BIGINT;
    phenotyper_id_var BIGINT;
    scientist_id_var BIGINT;
    accession_id_var BIGINT;
    session_id_var BIGINT;
BEGIN
    -- Verify that the species exists
    SELECT id INTO species_id_var FROM species WHERE common_name = species_common_name;
    IF species_id_var IS NULL THEN
        RAISE EXCEPTION 'Species % does not exist', species_common_name;
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

    -- Insert the scan session
    INSERT INTO gravi_scan_sessions (
        experiment_id, phenotyper_id,
        scan_mode, interval_seconds, duration_seconds, total_cycles,
        actual_duration_seconds, completed_at, cancelled
    ) VALUES (
        experiment_id_var, phenotyper_id_var,
        scan_mode_, interval_seconds_, duration_seconds_, total_cycles_,
        actual_duration_seconds_, completed_at_, cancelled_
    ) RETURNING id INTO session_id_var;

    RETURN session_id_var;
END;
$$;

REVOKE EXECUTE ON FUNCTION insert_gravi_scan_session FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insert_gravi_scan_session TO bloom_user, bloom_writer, bloom_admin;
