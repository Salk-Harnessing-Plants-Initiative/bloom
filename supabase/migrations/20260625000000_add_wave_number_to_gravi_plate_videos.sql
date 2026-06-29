-- Add wave_number to gravi_plate_videos so a plate's time-lapse is identified
-- per (experiment, plate, wave), not per (experiment, plate). Plate IDs are
-- reused across waves (the scanner restarts numbering each wave), so the old
-- key collapsed every wave's frames into a single video.

ALTER TABLE gravi_plate_videos ADD COLUMN IF NOT EXISTS wave_number INT;

-- Re-key uniqueness from (experiment, plate, session) to (experiment, plate,
-- wave): one canonical video per wave. session_id is retained as informational.
-- The COALESCE(...,-1) pattern keeps the index NULL-safe (mirrors
-- gravi_scan_metadata_accession), so a genuinely wave-less plate stays unique.
DROP INDEX IF EXISTS idx_gravi_plate_videos_unique;

-- The new key drops session_id, so any pre-existing rows that differed only by
-- session would now collide and block the unique index. Keep the most recent
-- per (experiment, plate, wave) before enforcing uniqueness. These rows are
-- regenerable metadata (the render job re-creates them per wave).
DELETE FROM gravi_plate_videos a
USING gravi_plate_videos b
WHERE a.experiment_id = b.experiment_id
  AND a.plate_id = b.plate_id
  AND COALESCE(a.wave_number, -1) = COALESCE(b.wave_number, -1)
  AND (a.generated_at, a.id) < (b.generated_at, b.id);

CREATE UNIQUE INDEX idx_gravi_plate_videos_unique
    ON gravi_plate_videos (experiment_id, plate_id, COALESCE(wave_number, -1));
