CREATE OR REPLACE FUNCTION insert_cyl_qc_codes(qc_codes json)
RETURNS void
LANGUAGE PLPGSQL
AS $$
BEGIN
    -- Insert data into cyl_qc_codes table
    INSERT INTO cyl_qc_codes (plant_id, value)
    SELECT
      cyl_plants_extended.plant_id AS plant_id,
      qc_code_data.qc_code AS value
    FROM
    (SELECT 
        (elem->>'experiment_id')::bigint AS experiment_id,
        elem->>'plant_qr_code' AS plant_qr_code,
        elem->>'qc_code' AS qc_code
    FROM 
        json_array_elements(qc_codes) AS elem
    ) AS qc_code_data
    JOIN cyl_plants_extended
    ON qc_code_data.plant_qr_code = cyl_plants_extended.qr_code
    AND qc_code_data.experiment_id = cyl_plants_extended.experiment_id;
END;
$$;