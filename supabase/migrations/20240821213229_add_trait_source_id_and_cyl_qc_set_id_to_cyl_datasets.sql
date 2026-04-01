
ALTER TABLE cyl_datasets ADD COLUMN trait_source_id BIGINT REFERENCES cyl_trait_sources(id);
ALTER TABLE cyl_datasets ADD COLUMN cyl_qc_set_id BIGINT REFERENCES cyl_qc_sets(id);

CREATE OR REPLACE FUNCTION create_cyl_dataset(name text, experiment_id bigint, trait_source_id bigint, qc_set_name json, timepoints json)
RETURNS void
LANGUAGE PLPGSQL
AS $$
DECLARE
    cyl_dataset_id bigint; -- ID of the new dataset
    qc_set_id bigint;
    _experiment_id bigint := experiment_id;
    _timepoints json := timepoints;
    _qc_set_name json := qc_set_name;
    _trait_source_id bigint := trait_source_id;
BEGIN
    -- Get the qc_set_id
    IF _qc_set_name IS NOT NULL THEN
        SELECT id INTO qc_set_id
        FROM cyl_qc_sets
        WHERE name = _qc_set_name::text;
    ELSE
        qc_set_id := NULL;
    END IF;

    -- Create the set
    INSERT INTO cyl_datasets (name, experiment_id, timepoints, cyl_qc_set_id, trait_source_id) VALUES (name, _experiment_id, _timepoints, qc_set_id, _trait_source_id) RETURNING id INTO cyl_dataset_id;

    -- Insert data into cyl_qc_codes table and capture the generated id
    INSERT INTO cyl_dataset_traits (trait_id, dataset_id)
    SELECT cyl_scan_traits.id as trait_id, cyl_dataset_id as dataset_id
    FROM cyl_scan_traits
    JOIN cyl_scans
    ON cyl_scan_traits.scan_id = cyl_scans.id
    JOIN cyl_plants_extended
    ON cyl_plants_extended.plant_id = cyl_scans.plant_id
    WHERE cyl_scan_traits.source_id = _trait_source_id
    AND cyl_plants_extended.experiment_id = _experiment_id
    AND (_timepoints IS NULL OR cyl_scans.plant_age_days IN (SELECT json_array_elements_text(_timepoints)::int))
    AND (qc_set_name IS NULL OR cyl_plants_extended.plant_id NOT IN (
      SELECT DISTINCT cyl_qc_codes.plant_id
      FROM cyl_qc_codes
      JOIN cyl_qc_set_codes ON cyl_qc_codes.id = cyl_qc_set_codes.code_id
      JOIN cyl_qc_sets ON cyl_qc_sets.id = cyl_qc_set_codes.set_id
      WHERE cyl_qc_sets.name = qc_set_name::text
    ));

END;
$$;
