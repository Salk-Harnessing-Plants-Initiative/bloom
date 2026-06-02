-- Fix NULL-ordering bug in both home-page widget views.
--
-- The original views ordered by uploaded_at DESC, which in Postgres defaults
-- to NULLS FIRST. Result: any (scanner, wave) pair containing a row with
-- NULL uploaded_at had that NULL row win DISTINCT ON; any scanner whose
-- per-pair latest was NULL beat scanners with real timestamps in the
-- ROW_NUMBER ranking. Cards rendered without a date even though the same
-- scanner had plenty of real timestamps in other waves.
--
-- This migration recreates both views with NULLS LAST so real timestamps
-- always win over NULLs in both the per-pair pick and the per-scanner rank.
-- View column shapes are unchanged — TypeScript callers don't need updates.

DROP VIEW IF EXISTS recent_experiments_by_cyl_scanner;

CREATE VIEW recent_experiments_by_cyl_scanner
WITH (security_invoker = true) AS
WITH latest_per_pair AS (
  SELECT DISTINCT ON (s.scanner_id, w.id)
    s.scanner_id,
    w.id           AS wave_id,
    w.number       AS wave_number,
    w.name         AS wave_name,
    e.id           AS experiment_id,
    e.name         AS experiment_name,
    sp.id          AS species_id,
    sp.common_name AS species_common_name,
    s.plant_age_days,
    ph.first_name  AS phenotyper_first_name,
    ph.last_name   AS phenotyper_last_name,
    s.uploaded_at  AS latest_upload_at
  FROM cyl_scans s
  JOIN cyl_plants      pl ON pl.id = s.plant_id
  JOIN cyl_waves       w  ON w.id  = pl.wave_id
  JOIN cyl_experiments e  ON e.id  = w.experiment_id
  LEFT JOIN species    sp ON sp.id = e.species_id
  LEFT JOIN phenotypers ph ON ph.id = s.phenotyper_id
  WHERE e.deleted_at IS NULL
  ORDER BY s.scanner_id, w.id, s.uploaded_at DESC NULLS LAST
),
ranked AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY scanner_id
      ORDER BY latest_upload_at DESC NULLS LAST
    ) AS rank_on_scanner
  FROM latest_per_pair
)
SELECT
  sc.id   AS scanner_id,
  sc.name AS scanner_name,
  r.experiment_id,
  r.experiment_name,
  r.species_id,
  r.species_common_name,
  r.wave_id,
  r.wave_number,
  r.wave_name,
  r.plant_age_days,
  r.phenotyper_first_name,
  r.phenotyper_last_name,
  r.latest_upload_at AS latest_upload_on_this_scanner_at,
  r.rank_on_scanner
FROM ranked r
JOIN cyl_scanners sc ON sc.id = r.scanner_id
WHERE r.rank_on_scanner <= 2
ORDER BY sc.name, r.rank_on_scanner;

GRANT SELECT ON recent_experiments_by_cyl_scanner TO authenticated;
GRANT SELECT ON recent_experiments_by_cyl_scanner TO bloom_user;
GRANT SELECT ON recent_experiments_by_cyl_scanner TO bloom_agent;
GRANT SELECT ON recent_experiments_by_cyl_scanner TO bloom_admin;

DO $$
BEGIN
  GRANT SELECT ON recent_experiments_by_cyl_scanner TO bloom_writer;
EXCEPTION WHEN undefined_object THEN
  NULL;
END$$;

-- Plate / gravi analog — same fix.
DROP VIEW IF EXISTS recent_phenotypes_by_plate_scanner;

CREATE VIEW recent_phenotypes_by_plate_scanner
WITH (security_invoker = true) AS
WITH scans_with_upload AS (
  SELECT
    s.id              AS scan_id,
    s.scanner_id,
    s.experiment_id,
    s.wave_number,
    s.phenotyper_id,
    s.plate_id,
    ses.scan_mode,
    MAX(img.uploaded_at) AS latest_upload_at
  FROM gravi_scans s
  LEFT JOIN gravi_images          img ON img.scan_id = s.id
  LEFT JOIN gravi_scan_sessions   ses ON ses.id     = s.session_id
  GROUP BY
    s.id, s.scanner_id, s.experiment_id, s.wave_number,
    s.phenotyper_id, s.plate_id, ses.scan_mode
  HAVING MAX(img.uploaded_at) IS NOT NULL
),
latest_per_pair AS (
  SELECT DISTINCT ON (scanner_id, experiment_id, wave_number)
    scanner_id,
    experiment_id,
    wave_number,
    phenotyper_id,
    plate_id,
    scan_mode,
    latest_upload_at
  FROM scans_with_upload
  ORDER BY scanner_id, experiment_id, wave_number, latest_upload_at DESC NULLS LAST
),
ranked AS (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY scanner_id
      ORDER BY latest_upload_at DESC NULLS LAST
    ) AS rank_on_scanner
  FROM latest_per_pair
)
SELECT
  sc.id   AS scanner_id,
  sc.name AS scanner_name,
  r.experiment_id,
  e.name  AS experiment_name,
  sp.id   AS species_id,
  sp.common_name AS species_common_name,
  r.wave_number,
  r.scan_mode,
  r.plate_id,
  ph.first_name AS phenotyper_first_name,
  ph.last_name  AS phenotyper_last_name,
  r.latest_upload_at AS latest_upload_on_this_scanner_at,
  r.rank_on_scanner
FROM ranked r
JOIN gravi_scanners      sc ON sc.id = r.scanner_id
JOIN gravi_experiments   e  ON e.id  = r.experiment_id
LEFT JOIN species        sp ON sp.id = e.species_id
LEFT JOIN phenotypers    ph ON ph.id = r.phenotyper_id
WHERE r.rank_on_scanner <= 2
ORDER BY sc.name, r.rank_on_scanner;

GRANT SELECT ON recent_phenotypes_by_plate_scanner TO authenticated;
GRANT SELECT ON recent_phenotypes_by_plate_scanner TO bloom_user;
GRANT SELECT ON recent_phenotypes_by_plate_scanner TO bloom_agent;
GRANT SELECT ON recent_phenotypes_by_plate_scanner TO bloom_admin;
