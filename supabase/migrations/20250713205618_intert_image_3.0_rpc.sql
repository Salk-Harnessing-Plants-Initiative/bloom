DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'cyl_camera_settings' AND column_name = 'exposure_time'
  ) THEN
    ALTER TABLE cyl_camera_settings RENAME COLUMN exposure_time TO scanner_exposure_time;
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'cyl_camera_settings' AND column_name = 'gain'
  ) THEN
    ALTER TABLE cyl_camera_settings RENAME COLUMN gain TO scanner_gain;
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'cyl_camera_settings' AND column_name = 'brightness'
  ) THEN
    ALTER TABLE cyl_camera_settings RENAME COLUMN brightness TO scanner_brightness;
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'cyl_camera_settings' AND column_name = 'contrast'
  ) THEN
    ALTER TABLE cyl_camera_settings RENAME COLUMN contrast TO scanner_contrast;
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'cyl_camera_settings' AND column_name = 'gamma'
  ) THEN
    ALTER TABLE cyl_camera_settings RENAME COLUMN gamma TO scanner_gamma;
  END IF;

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'cyl_camera_settings' AND column_name = 'seconds_per_rot'
  ) THEN
    ALTER TABLE cyl_camera_settings RENAME COLUMN seconds_per_rot TO scanner_seconds_per_rot;
  END IF;
END $$;

ALTER TABLE cyl_camera_settings
ADD CONSTRAINT unique_camera_settings_combo
UNIQUE (scanner_exposure_time, scanner_gain, scanner_brightness, scanner_contrast, scanner_gamma, scanner_seconds_per_rot);

CREATE OR REPLACE FUNCTION public.insert_image_v3_0(species_common_name text, experiment text, wave_number integer, germ_day integer, germ_day_color text, plant_age_days integer, date_scanned_ date, device_name text, plant_qr_code text, accession_name text, frame_number_ integer, phenotyper_name text, phenotyper_email text, scientist_name text, scientist_email text, num_frames integer, exposure_time integer, gain integer, brightness integer, contrast integer, gamma integer, seconds_per_rot integer)
RETURNS bigint
LANGUAGE plpgsql
AS $function$
DECLARE
   species_id_var BIGINT;
   experiment_id_var BIGINT;
   wave_id_var BIGINT;
   accession_id_var BIGINT;
   plant_id_var BIGINT;
   scanner_id_var BIGINT;
   scan_id_var BIGINT;
   image_id_var BIGINT;
   status_var TEXT;
   phenotyper_id_var BIGINT;
   scientist_id_var BIGINT;
   cyl_camera_settings_id_var UUID;

BEGIN
   -- Verify that the species exists
   SELECT id INTO species_id_var FROM species WHERE common_name = species_common_name;
   IF species_id_var IS NULL THEN
       RAISE EXCEPTION 'Species % does not exist', species_common_name;
   END IF;

   -- Verify that the scanner exists
   SELECT id INTO scanner_id_var FROM cyl_scanners WHERE name = device_name;
   IF scanner_id_var IS NULL THEN
       RAISE EXCEPTION 'Scanner % does not exist', device_name;
   END IF;

   -- Upsert the experiment
   INSERT INTO cyl_experiments (species_id, name) VALUES (species_id_var, experiment)
   ON CONFLICT (species_id, name) DO NOTHING RETURNING id INTO experiment_id_var;

   -- If experiment was not inserted, then retrieve its id
   IF experiment_id_var IS NULL THEN
       SELECT id INTO experiment_id_var FROM cyl_experiments WHERE species_id = species_id_var AND name = experiment;
   END IF;

   -- Upsert the wave
   INSERT INTO cyl_waves (experiment_id, number) VALUES (experiment_id_var, wave_number)
   ON CONFLICT (experiment_id, number) DO NOTHING RETURNING id INTO wave_id_var;

   -- If wave was not inserted, then retrieve its id
   IF wave_id_var IS NULL THEN
       SELECT id INTO wave_id_var FROM cyl_waves WHERE experiment_id = experiment_id_var AND number = wave_number;
   END IF;

   -- Upsert the accession
   INSERT INTO accessions (name) VALUES (accession_name)
   ON CONFLICT (name) DO NOTHING RETURNING id INTO accession_id_var;

   -- If accession was not inserted, then retrieve its id
   IF accession_id_var IS NULL THEN
       SELECT id INTO accession_id_var FROM accessions WHERE name = accession_name;
   END IF;

   -- Upsert the plant
   INSERT INTO cyl_plants (wave_id, qr_code, germ_day, germ_day_color, accession_id) VALUES (wave_id_var, plant_qr_code, germ_day, germ_day_color, accession_id_var)
   ON CONFLICT (wave_id, qr_code) DO NOTHING RETURNING id INTO plant_id_var;

   -- If plant was not inserted, then retrieve its id
   IF plant_id_var IS NULL THEN
       SELECT id INTO plant_id_var FROM cyl_plants WHERE wave_id = wave_id_var AND qr_code = plant_qr_code;
   END IF;

   -- Upsert the phenotyper
   INSERT INTO phenotypers (first_name, email)
   VALUES (phenotyper_name, phenotyper_email)
   ON CONFLICT (email) DO NOTHING
   RETURNING id INTO phenotyper_id_var;

   -- If not inserted, fetch the existing one
   IF phenotyper_id_var IS NULL THEN
   SELECT id INTO phenotyper_id_var FROM phenotypers WHERE email = phenotyper_email;
   END IF;

   -- Upsert the scientist
   INSERT INTO cyl_scientists (scientist_name, email)
   VALUES (scientist_name, scientist_email)
   ON CONFLICT (email) DO NOTHING
   RETURNING id INTO scientist_id_var;

   -- If not inserted, fetch the existing one
   IF scientist_id_var IS NULL THEN
   SELECT id INTO scientist_id_var FROM cyl_scientists WHERE email = scientist_email;
   END IF;

   -- Upsert the scanner settings
   INSERT INTO cyl_camera_settings (scanner_exposure_time,scanner_gain,scanner_brightness,scanner_contrast,scanner_gamma,scanner_seconds_per_rot)
   VALUES (exposure_time,gain,brightness,contrast,gamma,seconds_per_rot)
   ON CONFLICT (scanner_exposure_time,scanner_gain,scanner_brightness,scanner_contrast,scanner_gamma,scanner_seconds_per_rot) DO NOTHING
   RETURNING id INTO cyl_camera_settings_id_var;

   -- If not inserted, fetch the existing one
   IF cyl_camera_settings_id_var IS NULL THEN
      SELECT id INTO cyl_camera_settings_id_var
      FROM cyl_camera_settings
      WHERE
        scanner_exposure_time = exposure_time AND
        scanner_gain = gain AND
        scanner_brightness = brightness AND
        scanner_contrast = contrast AND
        scanner_gamma = gamma AND
        scanner_seconds_per_rot = seconds_per_rot;
    END IF;

   -- Upsert the scan
   INSERT INTO cyl_scans (plant_id, scanner_id, phenotyper_id, scientist_id,  date_scanned, plant_age_days, cyl_camera_settings_id) VALUES (plant_id_var, scanner_id_var,phenotyper_id_var,scientist_id_var, date_scanned_, plant_age_days, cyl_camera_settings_id_var)
   ON CONFLICT (plant_id, date_scanned) DO NOTHING RETURNING id INTO scan_id_var;

   -- If scan was not inserted, then retrieve its id
   IF scan_id_var IS NULL THEN
       SELECT id INTO scan_id_var FROM cyl_scans WHERE plant_id = plant_id_var AND cyl_scans.date_scanned = date_scanned_;
   END IF;

   -- Upsert the image
   INSERT INTO cyl_images (scan_id, frame_number, status) VALUES (scan_id_var, frame_number_, 'PENDING')
   ON CONFLICT (scan_id, frame_number) DO NOTHING RETURNING id INTO image_id_var;

   -- If image was not inserted, then retrieve its id
   IF image_id_var IS NULL THEN
       SELECT id INTO image_id_var FROM cyl_images WHERE scan_id = scan_id_var AND frame_number = frame_number_;
   END IF;

   -- If the image status is SUCCESS, then return null
   SELECT status INTO status_var FROM cyl_images WHERE id = image_id_var;
   IF status_var = 'SUCCESS' THEN
       RETURN NULL;
   END IF;

   -- Return the image id if it was inserted or was pending
   RETURN image_id_var;

END;
$function$
