
CREATE TABLE rhizovision_experiments (
    id SERIAL PRIMARY KEY,
    experiment_name TEXT NOT NULL UNIQUE,
    scientist_name TEXT,
    scientist_email TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
); 

CREATE TABLE rhizovision_settings (
    id BIGSERIAL PRIMARY KEY,
    root_type TEXT NOT NULL,
    image_threshold_level INT,
    pixel_to_mm_conversion INT,
    root_pruning_threshold BIGINT
);

CREATE TABLE rhizovision_images (
    id SERIAL PRIMARY KEY,
    plant_id TEXT NOT NULL UNIQUE,
    feature_image_path TEXT[],
    segmentation_image_path TEXT[]
);

CREATE TABLE rhizovision_accessions (
    id SERIAL PRIMARY KEY,
    accession TEXT,
    type TEXT 
);

CREATE TABLE rhizovision_results (
    id SERIAL PRIMARY KEY,
    plant_id TEXT NOT NULL,
    accession INT REFERENCES rhizovision_accessions(id) ON DELETE SET NULL,
    experiment_id INT REFERENCES rhizovision_experiments(id) ON DELETE SET NULL,
    rhizovision_settings_id INT REFERENCES rhizovision_settings(id) ON DELETE SET NULL,
    number_of_root_tips INT,
    total_root_length INT,
    network_area INT,
    convex_area INT,
    solidity INT,
    lower_root_area INT,
    average_diameter INT,
    median_diameter INT,
    maximum_diameter INT,
    perimeter INT,
    volume INT,
    surface_area INT,
    other_features JSONB,

    CONSTRAINT fk_rviz_images FOREIGN KEY (barcode) REFERENCES rhizovision_images(barcode) ON DELETE SET NULL
);
