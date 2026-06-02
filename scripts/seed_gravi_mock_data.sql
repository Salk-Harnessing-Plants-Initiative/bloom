-- ============================================================================
-- Seed Script: GRAVI (plate) Mock Data
-- ============================================================================
-- Populates plate / gravitropism tables with realistic data so the
-- /app/plate-phenotypes/[speciesId] page renders populated rows on dev.
--
-- Run with:
--   docker exec -i bloom_v2_dev-db-dev-1 psql -U supabase_admin -d postgres \
--     < scripts/seed_gravi_mock_data.sql
--
-- Idempotent: re-running updates / re-inserts conflict-free.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. cyl_scientists  (used by gravi_experiments.scientist_id by convention)
-- ----------------------------------------------------------------------------
INSERT INTO cyl_scientists (id, scientist_name, email) VALUES
  (8001, 'Dr. Elena Petrova',  'elena.petrova@example.org'),
  (8002, 'Dr. Aiden Nakamura', 'aiden.nakamura@example.org')
ON CONFLICT (id) DO UPDATE
  SET scientist_name = EXCLUDED.scientist_name,
      email          = EXCLUDED.email;

-- ----------------------------------------------------------------------------
-- 2. gravi_experiments — wire up scientist_id / accession_id / system_name
-- ----------------------------------------------------------------------------
UPDATE gravi_experiments
SET scientist_id = 8001,
    accession_id = 9001,           -- indi-1
    system_name  = 'gravi-rig-01'
WHERE id = 9101;

UPDATE gravi_experiments
SET scientist_id = 8002,
    accession_id = 9002,           -- indi-2
    system_name  = 'gravi-rig-02'
WHERE id = 9102;

-- ----------------------------------------------------------------------------
-- 3. gravi_scan_sessions — backfill cycles/duration on the existing two
--    sessions, plus add a 2nd/3rd session per experiment so the UI shows the
--    "N sessions" indicator.
-- ----------------------------------------------------------------------------
UPDATE gravi_scan_sessions
SET total_cycles            = 24,
    duration_seconds        = 7200,   -- planned 2h
    actual_duration_seconds = 7320    -- ran ~2h 2m
WHERE id = 9101;

UPDATE gravi_scan_sessions
SET total_cycles            = 12,
    duration_seconds        = 3600,
    actual_duration_seconds = 3540
WHERE id = 9102;

INSERT INTO gravi_scan_sessions
  (id, experiment_id, phenotyper_id, scan_mode, total_cycles,
   duration_seconds, actual_duration_seconds, started_at, completed_at)
VALUES
  (8101, 9101, 9001, 'continuous', 36, 10800, 11020,
     '2026-05-30 09:00:00+00', '2026-05-30 12:03:40+00'),
  (8102, 9101, 9002, 'continuous', 48, 14400, 14380,
     '2026-05-31 14:00:00+00', '2026-05-31 17:59:40+00'),
  (8103, 9102, 9002, 'single',     6,  1800,  1750,
     '2026-05-29 10:00:00+00', '2026-05-29 10:29:10+00')
ON CONFLICT (id) DO UPDATE
  SET total_cycles            = EXCLUDED.total_cycles,
      duration_seconds        = EXCLUDED.duration_seconds,
      actual_duration_seconds = EXCLUDED.actual_duration_seconds,
      started_at              = EXCLUDED.started_at,
      completed_at            = EXCLUDED.completed_at;

-- ----------------------------------------------------------------------------
-- 4. gravi_scans — more rows with distinct plate_ids so the UI shows
--    "N plates". Each plate_id below must be distinct per experiment to count.
-- ----------------------------------------------------------------------------
INSERT INTO gravi_scans
  (id, experiment_id, phenotyper_id, scanner_id, session_id, plate_id,
   cycle_number, capture_date, grid_mode, plate_index, resolution, format,
   wave_number, uploaded_at)
VALUES
  -- Experiment 9101 — 4 distinct plates across 2 sessions
  (8201, 9101, 9001, 9101, 8101, 'PLATE-A2', 1, '2026-05-30 09:05:00+00',
     '3x3', 'A2', 1200, 'jpeg', 1, '2026-05-30 09:06:00+00'),
  (8202, 9101, 9001, 9101, 8101, 'PLATE-A3', 1, '2026-05-30 09:10:00+00',
     '3x3', 'A3', 1200, 'jpeg', 1, '2026-05-30 09:11:00+00'),
  (8203, 9101, 9002, 9101, 8102, 'PLATE-A4', 1, '2026-05-31 14:05:00+00',
     '3x3', 'A4', 1200, 'jpeg', 2, '2026-05-31 14:06:00+00'),
  -- Experiment 9102 — 2 more distinct plates
  (8204, 9102, 9002, 9101, 8103, 'PLATE-B2', 1, '2026-05-29 10:05:00+00',
     '3x3', 'B2', 1200, 'jpeg', 1, '2026-05-29 10:06:00+00'),
  (8205, 9102, 9002, 9101, 8103, 'PLATE-B3', 1, '2026-05-29 10:10:00+00',
     '3x3', 'B3', 1200, 'jpeg', 1, '2026-05-29 10:11:00+00')
ON CONFLICT (id) DO UPDATE
  SET cycle_number = EXCLUDED.cycle_number,
      wave_number  = EXCLUDED.wave_number,
      session_id   = EXCLUDED.session_id,
      plate_id     = EXCLUDED.plate_id,
      uploaded_at  = EXCLUDED.uploaded_at;

-- ----------------------------------------------------------------------------
-- 5. gravi_scan_metadata_accession  (one row per plate × experiment × wave)
-- ----------------------------------------------------------------------------
INSERT INTO gravi_scan_metadata_accession
  (id, accession_id, plate_id, accession_name, wave_number, custom_note)
VALUES
  (8301, 9001, 'PLATE-A1', 'indi-1', 1, NULL),
  (8302, 9001, 'PLATE-A2', 'indi-1', 1, NULL),
  (8303, 9001, 'PLATE-A3', 'indi-1', 1, NULL),
  (8304, 9001, 'PLATE-A4', 'indi-1', 2, NULL),
  (8311, 9002, 'PLATE-B1', 'indi-2', 1, NULL),
  (8312, 9002, 'PLATE-B2', 'indi-2', 1, NULL),
  (8313, 9002, 'PLATE-B3', 'indi-2', 1, NULL)
ON CONFLICT (id) DO UPDATE
  SET accession_name = EXCLUDED.accession_name,
      wave_number    = EXCLUDED.wave_number;

-- Point each gravi_scan at its plate-level metadata row
UPDATE gravi_scans SET metadata_id = 8301 WHERE id = 9101;
UPDATE gravi_scans SET metadata_id = 8302 WHERE id = 8201;
UPDATE gravi_scans SET metadata_id = 8303 WHERE id = 8202;
UPDATE gravi_scans SET metadata_id = 8304 WHERE id = 8203;
UPDATE gravi_scans SET metadata_id = 8311 WHERE id = 9102;
UPDATE gravi_scans SET metadata_id = 8312 WHERE id = 8204;
UPDATE gravi_scans SET metadata_id = 8313 WHERE id = 8205;

-- ----------------------------------------------------------------------------
-- 6. gravi_scan_metadata_sections  (sections within a plate)
--    Wipe + reinsert so re-runs survive section_id reshuffles. Cascades to
--    gravi_scan_metadata_section_plants via FK ON DELETE CASCADE.
-- ----------------------------------------------------------------------------
DELETE FROM gravi_scan_metadata_sections
  WHERE metadata_id IN (8301, 8302, 8303, 8304, 8311, 8312, 8313);

INSERT INTO gravi_scan_metadata_sections
  (id, metadata_id, plate_section_id, medium)
VALUES
  (8401, 8301, 'top',          'MS 0.5x'),
  (8402, 8301, 'upper-middle', 'MS 0.5x'),
  (8403, 8301, 'lower-middle', 'MS 0.5x'),
  (8410, 8301, 'bottom',       'MS 0.5x'),
  (8404, 8302, 'top',    'MS 0.5x'),
  (8405, 8302, 'bottom', 'MS 0.5x'),
  (8406, 8303, 'top',    'MS 0.5x'),
  (8407, 8303, 'bottom', 'MS 0.5x'),
  (8408, 8304, 'top',    'MS + IAA'),
  (8409, 8304, 'bottom', 'MS + IAA'),
  (8411, 8311, 'top',    'MS 1x'),
  (8412, 8311, 'bottom', 'MS 1x'),
  (8413, 8312, 'top',    'MS 1x'),
  (8414, 8312, 'bottom', 'MS 1x'),
  (8415, 8313, 'top',    'MS 1x')
ON CONFLICT (metadata_id, plate_section_id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 7. gravi_scan_metadata_section_plants  (plant_qr per section)
-- ----------------------------------------------------------------------------
INSERT INTO gravi_scan_metadata_section_plants (id, section_id, plant_qr)
VALUES
  (8501, 8401, 'QR-A1-T1'),
  (8502, 8401, 'QR-A1-T2'),
  (8503, 8402, 'QR-A1-UM1'),
  (8512, 8403, 'QR-A1-LM1'),
  (8504, 8410, 'QR-A1-B1'),
  (8505, 8410, 'QR-A1-B2'),
  (8506, 8404, 'QR-A2-T1'),
  (8507, 8405, 'QR-A2-B1'),
  (8508, 8406, 'QR-A3-T1'),
  (8509, 8407, 'QR-A3-B1'),
  (8510, 8408, 'QR-A4-T1'),
  (8511, 8409, 'QR-A4-B1'),
  (8520, 8411, 'QR-B1-T1'),
  (8521, 8412, 'QR-B1-B1'),
  (8522, 8413, 'QR-B2-T1'),
  (8523, 8414, 'QR-B2-B1'),
  (8524, 8415, 'QR-B3-T1')
ON CONFLICT (section_id, plant_qr) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 8. gravi_images  (object_path under bucket "graviscan-images")
-- ----------------------------------------------------------------------------
INSERT INTO gravi_images (id, scan_id, object_path)
VALUES
  (8601, 8201, 'demo/plate-a2-cycle1.jpg'),
  (8602, 8202, 'demo/plate-a3-cycle1.jpg'),
  (8603, 8203, 'demo/plate-a4-cycle1.jpg'),
  (8604, 8204, 'demo/plate-b2-cycle1.jpg'),
  (8605, 8205, 'demo/plate-b3-cycle1.jpg')
ON CONFLICT (scan_id) DO UPDATE
  SET object_path = EXCLUDED.object_path;

-- ----------------------------------------------------------------------------
-- 9. PLATE-A1 time series — 12 cycles spanning ~3h to exercise the per-plate
--    time-series page's default window (last 2h, max 6 frames) and "show all"
--    toggle. Spaced 15 min apart, all tied to session 9101 / phenotyper 9001.
-- ----------------------------------------------------------------------------
INSERT INTO gravi_scans
  (id, experiment_id, phenotyper_id, scanner_id, session_id, plate_id,
   cycle_number, capture_date, grid_mode, plate_index, resolution, format,
   wave_number, metadata_id, uploaded_at)
VALUES
  (8701, 9101, 9001, 9101, 9101, 'PLATE-A1',  1, '2026-05-29 11:00:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 11:01:00+00'),
  (8702, 9101, 9001, 9101, 9101, 'PLATE-A1',  2, '2026-05-29 11:15:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 11:16:00+00'),
  (8703, 9101, 9001, 9101, 9101, 'PLATE-A1',  3, '2026-05-29 11:30:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 11:31:00+00'),
  (8704, 9101, 9001, 9101, 9101, 'PLATE-A1',  4, '2026-05-29 11:45:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 11:46:00+00'),
  (8705, 9101, 9001, 9101, 9101, 'PLATE-A1',  5, '2026-05-29 12:00:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 12:01:00+00'),
  (8706, 9101, 9001, 9101, 9101, 'PLATE-A1',  6, '2026-05-29 12:15:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 12:16:00+00'),
  (8707, 9101, 9001, 9101, 9101, 'PLATE-A1',  7, '2026-05-29 12:30:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 12:31:00+00'),
  (8708, 9101, 9001, 9101, 9101, 'PLATE-A1',  8, '2026-05-29 12:45:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 12:46:00+00'),
  (8709, 9101, 9001, 9101, 9101, 'PLATE-A1',  9, '2026-05-29 13:00:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 13:01:00+00'),
  (8710, 9101, 9001, 9101, 9101, 'PLATE-A1', 10, '2026-05-29 13:15:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 13:16:00+00'),
  (8711, 9101, 9001, 9101, 9101, 'PLATE-A1', 11, '2026-05-29 13:31:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 13:32:00+00'),
  (8712, 9101, 9001, 9101, 9101, 'PLATE-A1', 12, '2026-05-29 13:45:00+00', '3x3', 'A1', 1200, 'jpeg', 1, 8301, '2026-05-29 13:46:00+00')
ON CONFLICT (id) DO UPDATE
  SET cycle_number = EXCLUDED.cycle_number,
      capture_date = EXCLUDED.capture_date,
      metadata_id  = EXCLUDED.metadata_id,
      uploaded_at  = EXCLUDED.uploaded_at;

INSERT INTO gravi_images (id, scan_id, object_path)
VALUES
  (8701, 8701, 'demo/plate-a1-cycle01.jpg'),
  (8702, 8702, 'demo/plate-a1-cycle02.jpg'),
  (8703, 8703, 'demo/plate-a1-cycle03.jpg'),
  (8704, 8704, 'demo/plate-a1-cycle04.jpg'),
  (8705, 8705, 'demo/plate-a1-cycle05.jpg'),
  (8706, 8706, 'demo/plate-a1-cycle06.jpg'),
  (8707, 8707, 'demo/plate-a1-cycle07.jpg'),
  (8708, 8708, 'demo/plate-a1-cycle08.jpg'),
  (8709, 8709, 'demo/plate-a1-cycle09.jpg'),
  (8710, 8710, 'demo/plate-a1-cycle10.jpg'),
  (8711, 8711, 'demo/plate-a1-cycle11.jpg'),
  (8712, 8712, 'demo/plate-a1-cycle12.jpg')
ON CONFLICT (scan_id) DO UPDATE
  SET object_path = EXCLUDED.object_path;

-- ----------------------------------------------------------------------------
-- 10. gravi_plate_videos  (canonical time-lapse video per plate)
--     The bucket file does NOT exist on dev — the video viewer will show its
--     "no time-lapse video available" placeholder. Wired so the table lookup
--     path is exercised end-to-end.
-- ----------------------------------------------------------------------------
INSERT INTO gravi_plate_videos
  (id, experiment_id, plate_id, session_id, object_path,
   duration_seconds, frame_count, file_size_bytes, generated_at)
VALUES
  (8801, 9101, 'PLATE-A1', 9101, '9101/PLATE-A1.mp4',
     180, 13, 12345678, '2026-05-29 14:00:00+00')
ON CONFLICT (experiment_id, plate_id, COALESCE(session_id, -1)) DO UPDATE
  SET object_path     = EXCLUDED.object_path,
      duration_seconds = EXCLUDED.duration_seconds,
      frame_count     = EXCLUDED.frame_count,
      file_size_bytes = EXCLUDED.file_size_bytes,
      generated_at    = EXCLUDED.generated_at;

COMMIT;

-- ----------------------------------------------------------------------------
-- Verify
-- ----------------------------------------------------------------------------
SELECT
  e.id   AS experiment_id,
  e.name,
  csci.scientist_name AS scientist,
  acc.name            AS accession,
  COUNT(DISTINCT ses.id) AS sessions,
  COUNT(DISTINCT s.plate_id) FILTER (WHERE s.plate_id IS NOT NULL) AS plates
FROM gravi_experiments e
LEFT JOIN cyl_scientists      csci ON csci.id = e.scientist_id
LEFT JOIN accessions          acc  ON acc.id  = e.accession_id
LEFT JOIN gravi_scan_sessions ses  ON ses.experiment_id = e.id
LEFT JOIN gravi_scans         s    ON s.experiment_id   = e.id
GROUP BY e.id, e.name, csci.scientist_name, acc.name
ORDER BY e.id;
