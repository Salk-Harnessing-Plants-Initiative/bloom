ALTER TABLE species ADD CONSTRAINT common_name_uniqueness UNIQUE (common_name);
ALTER TABLE species ADD CONSTRAINT species_genus_uniqueness UNIQUE (genus, species);
ALTER TABLE cyl_experiments ADD CONSTRAINT cyl_experiments_uniqueness UNIQUE (species_id, name);
ALTER TABLE cyl_waves ADD CONSTRAINT cyl_waves_uniqueness UNIQUE (experiment_id, number);
ALTER TABLE cyl_plants ADD CONSTRAINT cyl_plants_uniqueness UNIQUE (wave_id, qr_code);
ALTER TABLE cyl_scanners ADD CONSTRAINT cyl_scanners_uniqueness UNIQUE (name);
ALTER TABLE cyl_scans ADD CONSTRAINT cyl_scans_uniqueness UNIQUE (plant_id, date_scanned);
ALTER TABLE cyl_images ADD CONSTRAINT cyl_images_uniqueness UNIQUE (scan_id, frame_number);
