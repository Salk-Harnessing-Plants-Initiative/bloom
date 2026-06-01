-- View powering the home-page "Recent phenotypes by plate scanner" widget
-- (the gravi-side analog of recent_experiments_by_cyl_scanner).
--
-- For each gravi_scanners row, returns up to 2 most-recently-uploaded
-- (experiment, wave_number) pairs. Each returned row carries the scan_mode
-- (single / continuous — gated by the gravi_scan_sessions.chk_scan_mode
-- check constraint) and the phenotyper name, so the client can render the
-- card without any follow-up join queries.
--
-- "Most recent" is computed as MAX(gravi_images.uploaded_at) across the
-- images attached to the scan — this matches the cyl view's "when did the
-- row land in the DB" semantics (cyl_scans.uploaded_at). Scans with no
-- uploaded images are excluded (HAVING clause).
--
-- security_invoker=true means RLS on the underlying tables is enforced with
-- the calling user's role (bloom_user / bloom_writer / bloom_admin /
-- bloom_agent) rather than the view owner's.

DROP VIEW IF EXISTS recent_phenotypes_by_plate_scanner;

CREATE VIEW recent_phenotypes_by_plate_scanner
WITH (security_invoker = true) AS
WITH scans_with_upload AS (
  -- For each scan, the most recent uploaded_at across its images.
  -- HAVING filters out scans that have no images yet.
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
  -- One row per (scanner, experiment, wave_number) — picked by most recent
  -- upload. DISTINCT ON + ORDER BY ... uploaded_at DESC gives the latest.
  SELECT DISTINCT ON (scanner_id, experiment_id, wave_number)
    scanner_id,
    experiment_id,
    wave_number,
    phenotyper_id,
    plate_id,
    scan_mode,
    latest_upload_at
  FROM scans_with_upload
  ORDER BY scanner_id, experiment_id, wave_number, latest_upload_at DESC
),
ranked AS (
  -- Within each scanner, rank pairs by most recent upload.
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

-- Grant SELECT to the bloom roles + authenticated. RLS on the underlying
-- tables enforces row-level visibility (security_invoker=true above).
GRANT SELECT ON recent_phenotypes_by_plate_scanner TO authenticated;
GRANT SELECT ON recent_phenotypes_by_plate_scanner TO bloom_user;
GRANT SELECT ON recent_phenotypes_by_plate_scanner TO bloom_agent;
GRANT SELECT ON recent_phenotypes_by_plate_scanner TO bloom_admin;
-- bloom_writer GRANT intentionally omitted — staging/dev may not have the
-- role created yet (its creation migration depends on supabase_admin
-- ownership of custom_access_token_hook). The role inherits SELECT from
-- bloom_user when it is created.
