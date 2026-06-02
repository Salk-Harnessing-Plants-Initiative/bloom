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
  (8201, 9101, 9001, 9101,8101, 'PLATE-A2', 9101,'2026-05-30 09:05:00+00',
     '3x3', 'A2', 1200, 'jpeg', 9101,'2026-05-30 09:06:00+00'),
  (8202, 9101, 9001, 9101,8101, 'PLATE-A3', 9101,'2026-05-30 09:10:00+00',
     '3x3', 'A3', 1200, 'jpeg', 9101,'2026-05-30 09:11:00+00'),
  (8203, 9101, 9002, 9101,8102, 'PLATE-A4', 9101,'2026-05-31 14:05:00+00',
     '3x3', 'A4', 1200, 'jpeg', 2, '2026-05-31 14:06:00+00'),
  -- Experiment 9102 — 2 more distinct plates
  (8204, 9102, 9002, 9101,8103, 'PLATE-B2', 9101,'2026-05-29 10:05:00+00',
     '3x3', 'B2', 1200, 'jpeg', 9101,'2026-05-29 10:06:00+00'),
  (8205, 9102, 9002, 9101,8103, 'PLATE-B3', 9101,'2026-05-29 10:10:00+00',
     '3x3', 'B3', 1200, 'jpeg', 9101,'2026-05-29 10:11:00+00')
ON CONFLICT (id) DO UPDATE
  SET session_id   = EXCLUDED.session_id,
      plate_id     = EXCLUDED.plate_id,
      uploaded_at  = EXCLUDED.uploaded_at;

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
