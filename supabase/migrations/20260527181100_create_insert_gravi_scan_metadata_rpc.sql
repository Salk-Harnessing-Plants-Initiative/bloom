-- insert_gravi_scan_metadata: per-plate metadata upsert called once per
-- unique plate in an upload batch. Ported from V1 production.
--
-- Writes three related rows in one transaction:
--   1. gravi_scan_metadata_accession (per-plate metadata, deduped via the
--      expression index on (accession_id, plate_id, COALESCE(wave_number,-1)))
--   2. gravi_scan_metadata_sections (one row per section in the plate)
--   3. gravi_scan_metadata_section_plants (plant QR → section mapping)
--
-- The `sections_` JSONB argument is structured as:
--   [{ "plate_section_id": "A", "medium": "MS",
--      "plant_qr": "SOY-W1-001" }, ...]
-- Wrapped in `jsonb_typeof = 'string'` handling because PostgREST sometimes
-- double-encodes nested JSON.
--
-- Returns the metadata_id (gravi_scan_metadata_accession.id), which the
-- desktop maps to its local plate accession id and passes as `metadata_id_`
-- when calling insert_gravi_image.

DROP FUNCTION IF EXISTS insert_gravi_scan_metadata;

CREATE OR REPLACE FUNCTION insert_gravi_scan_metadata(
    accession_name_ TEXT,
    plate_id_ TEXT,
    sections_ JSONB DEFAULT '[]'::jsonb,
    wave_number_ INT DEFAULT NULL,
    transplant_date_ TIMESTAMPTZ DEFAULT NULL,
    custom_note_ TEXT DEFAULT NULL
) RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    accession_id_var BIGINT;
    metadata_id_var BIGINT;
    section_id_var BIGINT;
    section_record JSONB;
    sections_arr JSONB;
BEGIN
    -- Handle double-encoded JSONB (PostgREST may send a JSON string scalar).
    IF jsonb_typeof(sections_) = 'string' THEN
        sections_arr := (sections_ #>> '{}')::JSONB;
    ELSE
        sections_arr := sections_;
    END IF;

    -- Upsert the accession.
    INSERT INTO accessions (name) VALUES (accession_name_)
    ON CONFLICT (name) DO NOTHING RETURNING id INTO accession_id_var;
    IF accession_id_var IS NULL THEN
        SELECT id INTO accession_id_var FROM accessions WHERE name = accession_name_;
    END IF;

    -- NULL-safe duplicate check on the metadata triplet.
    SELECT id INTO metadata_id_var FROM gravi_scan_metadata_accession
    WHERE accession_id = accession_id_var
      AND plate_id = plate_id_
      AND wave_number IS NOT DISTINCT FROM wave_number_;

    IF metadata_id_var IS NULL THEN
        INSERT INTO gravi_scan_metadata_accession
            (accession_id, plate_id, accession_name, wave_number, transplant_date, custom_note)
        VALUES (accession_id_var, plate_id_, accession_name_, wave_number_, transplant_date_, custom_note_)
        RETURNING id INTO metadata_id_var;
    END IF;

    -- Upsert sections + section-plant mappings.
    FOR section_record IN SELECT * FROM jsonb_array_elements(sections_arr)
    LOOP
        -- Skip sections with missing plate_section_id.
        IF (section_record->>'plate_section_id') IS NULL THEN
            CONTINUE;
        END IF;

        -- Upsert section.
        INSERT INTO gravi_scan_metadata_sections (metadata_id, plate_section_id, medium)
        VALUES (metadata_id_var, section_record->>'plate_section_id', section_record->>'medium')
        ON CONFLICT (metadata_id, plate_section_id) DO NOTHING
        RETURNING id INTO section_id_var;

        IF section_id_var IS NULL THEN
            SELECT id INTO section_id_var FROM gravi_scan_metadata_sections
            WHERE metadata_id = metadata_id_var
              AND plate_section_id = (section_record->>'plate_section_id');
        END IF;

        -- Insert plant mapping (skip if plant_qr missing).
        IF (section_record->>'plant_qr') IS NOT NULL THEN
            INSERT INTO gravi_scan_metadata_section_plants (section_id, plant_qr)
            VALUES (section_id_var, section_record->>'plant_qr')
            ON CONFLICT (section_id, plant_qr) DO NOTHING;
        END IF;
    END LOOP;

    RETURN metadata_id_var;
END;
$$;

REVOKE EXECUTE ON FUNCTION insert_gravi_scan_metadata FROM PUBLIC;
GRANT EXECUTE ON FUNCTION insert_gravi_scan_metadata TO bloom_user, bloom_writer, bloom_admin;
