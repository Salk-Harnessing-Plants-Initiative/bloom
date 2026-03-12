-- Add ON DELETE CASCADE to cyl_waves, cyl_plants, cyl_scans, and cyl_images

-- cyl_waves

ALTER TABLE cyl_waves
DROP CONSTRAINT cyl_waves_experiment_id_fkey;

ALTER TABLE cyl_waves
ADD CONSTRAINT cyl_waves_experiment_id_fkey FOREIGN KEY (experiment_id)
REFERENCES cyl_experiments (id)
ON DELETE CASCADE;


-- cyl_plants

ALTER TABLE cyl_plants
DROP CONSTRAINT cyl_plants_wave_id_fkey;

ALTER TABLE cyl_plants
ADD CONSTRAINT cyl_plants_wave_id_fkey FOREIGN KEY (wave_id)
REFERENCES cyl_waves (id)
ON DELETE CASCADE;


-- cyl_scans

ALTER TABLE cyl_scans
DROP CONSTRAINT cyl_scans_plant_id_fkey;

ALTER TABLE cyl_scans
ADD CONSTRAINT cyl_scans_plant_id_fkey FOREIGN KEY (plant_id)
REFERENCES cyl_plants (id)
ON DELETE CASCADE;


-- cyl_images

ALTER TABLE cyl_images
DROP CONSTRAINT cyl_images_scan_id_fkey;

ALTER TABLE cyl_images
ADD CONSTRAINT cyl_images_scan_id_fkey FOREIGN KEY (scan_id)
REFERENCES cyl_scans (id)
ON DELETE CASCADE;