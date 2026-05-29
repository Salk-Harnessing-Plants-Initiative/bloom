-- View powering the "Recent experiments by cylinder scanner" home-page widget.
--
-- For each cyl_scanners row, returns up to 2 most-recently-uploaded
-- (experiment, wave) pairs by the latest cyl_scans.uploaded_at on that
-- scanner. Each returned row carries the most recent scan's plant_age_days
-- and phenotyper, so the client can render the card directly without any
-- follow-up join queries.
--
-- We sort by uploaded_at (when the row landed in the DB) rather than
-- date_scanned (when the camera captured the image) so easier to track whats on bloom".
--
-- security_invoker=true means RLS on the underlying tables is enforced with
-- the calling user's role (bloom_user / bloom_writer / bloom_admin /
-- bloom_agent) rather than the view owner's. Combined with the explicit
-- deleted_at IS NULL filter, soft-deleted experiments are excluded.

DROP VIEW IF EXISTS recent_experiments_by_cyl_scanner;

CREATE VIEW recent_experiments_by_cyl_scanner
WITH (security_invoker = true) AS
WITH latest_per_pair AS (
  -- One row per (scanner, wave) pair — the row from the most recent UPLOAD
  -- of that pair on that scanner. DISTINCT ON picks the first row per
  -- (scanner_id, wave_id) group; ORDER BY ... uploaded_at DESC makes
  -- "first" mean "most recently uploaded".
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
  ORDER BY s.scanner_id, w.id, s.uploaded_at DESC
),
ranked AS (
  -- Within each scanner, rank (experiment, wave) pairs by most-recent upload.
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY scanner_id
      ORDER BY latest_upload_at DESC
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

-- Grant SELECT to the bloom roles + authenticated. RLS on the underlying
-- tables enforces row-level visibility (security_invoker=true above).
GRANT SELECT ON recent_experiments_by_cyl_scanner TO authenticated;
GRANT SELECT ON recent_experiments_by_cyl_scanner TO bloom_user;
GRANT SELECT ON recent_experiments_by_cyl_scanner TO bloom_writer;
GRANT SELECT ON recent_experiments_by_cyl_scanner TO bloom_agent;
GRANT SELECT ON recent_experiments_by_cyl_scanner TO bloom_admin;
