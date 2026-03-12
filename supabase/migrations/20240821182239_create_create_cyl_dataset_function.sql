CREATE OR REPLACE FUNCTION create_cyl_dataset(name text, experiment_id bigint, trait_source_id bigint, qc_set_name json, timepoints json)
RETURNS void
LANGUAGE PLPGSQL
AS $$
DECLARE
    cyl_dataset_id bigint; -- ID of the new dataset
BEGIN
    -- Create the set
    INSERT INTO cyl_datasets (name) VALUES (name) RETURNING id INTO cyl_dataset_id;

    -- Insert data into cyl_qc_codes table and capture the generated id
    INSERT INTO cyl_dataset_traits (trait_id, dataset_id)
    SELECT cyl_scan_traits.id as trait_id, cyl_dataset_id as dataset_id
    FROM cyl_scan_traits
    JOIN cyl_scans
    ON cyl_scan_traits.scan_id = cyl_scans.id
    JOIN cyl_plants_extended
    ON cyl_plants_extended.plant_id = cyl_scans.plant_id
    WHERE cyl_scan_traits.source_id = trait_source_id
    AND cyl_plants_extended.experiment_id = experiment_id
    AND (timepoints IS NULL OR cyl_scans.plant_age_days IN (SELECT json_array_elements_text(timepoints)::int))
    AND (qc_set_name IS NULL OR cyl_plants_extended.plant_id NOT IN (
      SELECT DISTINCT cyl_qc_codes.plant_id
      FROM cyl_qc_codes
      JOIN cyl_qc_set_codes ON cyl_qc_codes.id = cyl_qc_set_codes.code_id
      JOIN cyl_qc_sets ON cyl_qc_sets.id = cyl_qc_set_codes.set_id
      WHERE cyl_qc_sets.name = qc_set_name::text
    ));

END;
$$;
