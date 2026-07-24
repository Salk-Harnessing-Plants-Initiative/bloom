-- Flattened read model for the cylinder plant search bar: one row per plant
-- with its barcode, accession, species, and experiment. security_invoker so
-- RLS on the base tables is enforced with the caller's role.

CREATE OR REPLACE VIEW cyl_plant_search
WITH (security_invoker = true) AS
SELECT
  p.id           AS plant_id,
  p.qr_code      AS qr_code,
  p.accession_id AS accession_id,
  a.name         AS accession_name,
  sp.id          AS species_id,
  sp.common_name AS species_name,
  e.id           AS experiment_id,
  e.name         AS experiment_name,
  w.id           AS wave_id
FROM cyl_plants p
JOIN cyl_waves       w  ON w.id  = p.wave_id
JOIN cyl_experiments e  ON e.id  = w.experiment_id
LEFT JOIN accessions a  ON a.id  = p.accession_id
LEFT JOIN species    sp ON sp.id = e.species_id
-- sp is LEFT-joined: IS NULL also passes plants with no species, and excludes soft-deleted ones.
WHERE e.deleted_at IS NULL
  AND sp.deleted_at IS NULL;

GRANT SELECT ON cyl_plant_search TO authenticated;
GRANT SELECT ON cyl_plant_search TO bloom_user;
GRANT SELECT ON cyl_plant_search TO bloom_admin;
GRANT SELECT ON cyl_plant_search TO bloom_agent;

-- Supabase default privileges grant SELECT to anon; the search view must not
-- be world-readable, so revoke it explicitly.
REVOKE ALL ON cyl_plant_search FROM anon;
