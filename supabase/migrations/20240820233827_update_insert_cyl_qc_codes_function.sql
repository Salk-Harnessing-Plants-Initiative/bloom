CREATE OR REPLACE FUNCTION insert_cyl_qc_codes(qc_codes json)
RETURNS void
LANGUAGE PLPGSQL
AS $$
DECLARE
    qc_code_data RECORD;  -- Declaring the loop variable as a RECORD type
    qc_code_id bigint;
BEGIN
    -- Loop through each element in the JSON array
    FOR qc_code_data IN 
    SELECT 
        (elem->>'experiment_id')::bigint AS experiment_id,
        elem->>'plant_qr_code' AS plant_qr_code,
        elem->>'qc_code' AS qc_code,
        (elem->>'qc_set_id')::bigint AS qc_set_id
    FROM 
        json_array_elements(qc_codes) AS elem
    LOOP
        -- Insert data into cyl_qc_codes table and capture the generated id
        INSERT INTO cyl_qc_codes (plant_id, value)
        SELECT
          cyl_plants_extended.plant_id AS plant_id,
          qc_code_data.qc_code AS value
        FROM
          cyl_plants_extended
        WHERE
          qc_code_data.plant_qr_code = cyl_plants_extended.qr_code
          AND qc_code_data.experiment_id = cyl_plants_extended.experiment_id
        RETURNING id INTO qc_code_id;

        -- Insert data into the association table
        INSERT INTO cyl_qc_set_codes (code_id, set_id)
        VALUES (qc_code_id, qc_code_data.qc_set_id);
    END LOOP;
END;
$$;
