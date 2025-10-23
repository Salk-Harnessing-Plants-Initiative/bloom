-- Index for cyl_waves
CREATE INDEX cyl_waves_experiment_id_idx ON cyl_waves (experiment_id);

-- Index for cyl_plants
CREATE INDEX cyl_plants_wave_id_idx ON cyl_plants (wave_id);

-- Index for cyl_scans
CREATE INDEX cyl_scans_plant_id_idx ON cyl_scans (plant_id);

-- Index for cyl_images
CREATE INDEX cyl_images_scan_id_idx ON cyl_images (scan_id);
