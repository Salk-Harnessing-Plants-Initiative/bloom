-- ============================================================================
-- Seed Script: CYL Phenotyping Mock Data
-- ============================================================================
-- This script populates the cylinder phenotyping tables with realistic test data
-- for developing and testing analytical tools.
--
-- Run with: docker exec -i db-dev psql -U postgres -d postgres < scripts/seed_cyl_mock_data.sql
--
-- Data Created:
--   - 13 phenotype traits
--   - 8 plant accessions (soybean varieties)
--   - 3 planting waves
--   - 40 plants across waves
--   - 240 scans (6 per plant over 3 weeks)
--   - 3,120 scan trait measurements
--
-- Data Patterns:
--   - Plants with qr_code ending in '-001' have higher root_width_max (outliers)
--   - Williams-82 accession has higher crown_angle_proximal
--   - PI-416937 accession has longer primary_length
--   - All traits show growth over time (correlated with plant_age_days)
-- ============================================================================

-- ============================================================================
-- 1. TRAITS - Common phenotype measurements
-- ============================================================================
INSERT INTO cyl_traits (name) VALUES
    ('root_width_max'),
    ('root_width_mean'),
    ('root_depth'),
    ('crown_angle_proximal'),
    ('crown_angle_distal'),
    ('crown_length'),
    ('lateral_count'),
    ('lateral_angle_mean'),
    ('primary_length'),
    ('stem_diameter'),
    ('shoot_height'),
    ('leaf_area'),
    ('biomass_estimate')
ON CONFLICT (name) DO NOTHING;

-- ============================================================================
-- 2. ACCESSIONS - Plant varieties/genotypes
-- ============================================================================
INSERT INTO accessions (name) VALUES
    ('Williams-82'),
    ('Clark'),
    ('Lee'),
    ('PI-416937'),
    ('PI-398223'),
    ('Essex'),
    ('Forrest'),
    ('Pioneer-93Y92')
ON CONFLICT (name) DO NOTHING;

-- ============================================================================
-- 3. WAVES - Additional planting waves for Experiment 1
-- ============================================================================
-- Fix sequence before inserting
SELECT setval('cyl_waves_id_seq', (SELECT COALESCE(MAX(id), 0) + 1 FROM cyl_waves), false);

INSERT INTO cyl_waves (experiment_id, number, name) VALUES
    (1, 2, 'Wave 2 - Early Spring'),
    (1, 3, 'Wave 3 - Late Spring')
ON CONFLICT (experiment_id, number) DO NOTHING;

-- ============================================================================
-- 4. PLANTS - 40 plants across waves and accessions
-- ============================================================================
-- Fix sequence before inserting
SELECT setval('cyl_plants_id_seq', (SELECT COALESCE(MAX(id), 0) + 1 FROM cyl_plants), false);

DO $$
DECLARE
    acc_williams bigint;
    acc_clark bigint;
    acc_lee bigint;
    acc_pi416 bigint;
    acc_pi398 bigint;
    acc_essex bigint;
    acc_forrest bigint;
    acc_pioneer bigint;
    wave1_id bigint;
    wave2_id bigint;
    wave3_id bigint;
BEGIN
    -- Get accession IDs
    SELECT id INTO acc_williams FROM accessions WHERE name = 'Williams-82';
    SELECT id INTO acc_clark FROM accessions WHERE name = 'Clark';
    SELECT id INTO acc_lee FROM accessions WHERE name = 'Lee';
    SELECT id INTO acc_pi416 FROM accessions WHERE name = 'PI-416937';
    SELECT id INTO acc_pi398 FROM accessions WHERE name = 'PI-398223';
    SELECT id INTO acc_essex FROM accessions WHERE name = 'Essex';
    SELECT id INTO acc_forrest FROM accessions WHERE name = 'Forrest';
    SELECT id INTO acc_pioneer FROM accessions WHERE name = 'Pioneer-93Y92';

    -- Get wave IDs
    SELECT id INTO wave1_id FROM cyl_waves WHERE experiment_id = 1 AND number = 1;
    SELECT id INTO wave2_id FROM cyl_waves WHERE experiment_id = 1 AND number = 2;
    SELECT id INTO wave3_id FROM cyl_waves WHERE experiment_id = 1 AND number = 3;

    -- Insert plants for Wave 1 (15 plants)
    INSERT INTO cyl_plants (qr_code, wave_id, germ_day, accession_id, created_at) VALUES
        ('SOY-W1-001', wave1_id, 3, acc_williams, '2024-01-15'),
        ('SOY-W1-002', wave1_id, 4, acc_williams, '2024-01-15'),
        ('SOY-W1-003', wave1_id, 3, acc_clark, '2024-01-15'),
        ('SOY-W1-004', wave1_id, 5, acc_clark, '2024-01-15'),
        ('SOY-W1-005', wave1_id, 3, acc_lee, '2024-01-15'),
        ('SOY-W1-006', wave1_id, 4, acc_lee, '2024-01-15'),
        ('SOY-W1-007', wave1_id, 3, acc_pi416, '2024-01-15'),
        ('SOY-W1-008', wave1_id, 6, acc_pi416, '2024-01-15'),
        ('SOY-W1-009', wave1_id, 3, acc_pi398, '2024-01-15'),
        ('SOY-W1-010', wave1_id, 4, acc_essex, '2024-01-15'),
        ('SOY-W1-011', wave1_id, 3, acc_essex, '2024-01-15'),
        ('SOY-W1-012', wave1_id, 5, acc_forrest, '2024-01-15'),
        ('SOY-W1-013', wave1_id, 3, acc_forrest, '2024-01-15'),
        ('SOY-W1-014', wave1_id, 4, acc_pioneer, '2024-01-15'),
        ('SOY-W1-015', wave1_id, 3, acc_pioneer, '2024-01-15')
    ON CONFLICT (wave_id, qr_code) DO NOTHING;

    -- Insert plants for Wave 2 (15 plants)
    INSERT INTO cyl_plants (qr_code, wave_id, germ_day, accession_id, created_at) VALUES
        ('SOY-W2-001', wave2_id, 4, acc_williams, '2024-02-01'),
        ('SOY-W2-002', wave2_id, 3, acc_williams, '2024-02-01'),
        ('SOY-W2-003', wave2_id, 5, acc_clark, '2024-02-01'),
        ('SOY-W2-004', wave2_id, 3, acc_clark, '2024-02-01'),
        ('SOY-W2-005', wave2_id, 4, acc_lee, '2024-02-01'),
        ('SOY-W2-006', wave2_id, 3, acc_pi416, '2024-02-01'),
        ('SOY-W2-007', wave2_id, 5, acc_pi416, '2024-02-01'),
        ('SOY-W2-008', wave2_id, 3, acc_pi398, '2024-02-01'),
        ('SOY-W2-009', wave2_id, 4, acc_pi398, '2024-02-01'),
        ('SOY-W2-010', wave2_id, 3, acc_essex, '2024-02-01'),
        ('SOY-W2-011', wave2_id, 5, acc_forrest, '2024-02-01'),
        ('SOY-W2-012', wave2_id, 3, acc_forrest, '2024-02-01'),
        ('SOY-W2-013', wave2_id, 4, acc_pioneer, '2024-02-01'),
        ('SOY-W2-014', wave2_id, 3, acc_pioneer, '2024-02-01'),
        ('SOY-W2-015', wave2_id, 5, acc_williams, '2024-02-01')
    ON CONFLICT (wave_id, qr_code) DO NOTHING;

    -- Insert plants for Wave 3 (10 plants)
    INSERT INTO cyl_plants (qr_code, wave_id, germ_day, accession_id, created_at) VALUES
        ('SOY-W3-001', wave3_id, 3, acc_williams, '2024-03-01'),
        ('SOY-W3-002', wave3_id, 4, acc_clark, '2024-03-01'),
        ('SOY-W3-003', wave3_id, 3, acc_lee, '2024-03-01'),
        ('SOY-W3-004', wave3_id, 5, acc_pi416, '2024-03-01'),
        ('SOY-W3-005', wave3_id, 3, acc_pi398, '2024-03-01'),
        ('SOY-W3-006', wave3_id, 4, acc_essex, '2024-03-01'),
        ('SOY-W3-007', wave3_id, 3, acc_forrest, '2024-03-01'),
        ('SOY-W3-008', wave3_id, 5, acc_pioneer, '2024-03-01'),
        ('SOY-W3-009', wave3_id, 3, acc_williams, '2024-03-01'),
        ('SOY-W3-010', wave3_id, 4, acc_clark, '2024-03-01')
    ON CONFLICT (wave_id, qr_code) DO NOTHING;
END $$;

-- ============================================================================
-- 5. SCANS - Time series scans for each plant (6 scans over 3 weeks)
-- ============================================================================
-- Fix sequence before inserting
SELECT setval('cyl_scans_id_seq', (SELECT COALESCE(MAX(id), 0) + 1 FROM cyl_scans), false);

INSERT INTO cyl_scans (plant_id, date_scanned, scanner_id, plant_age_days, uploaded_at)
SELECT
    p.id as plant_id,
    (p.created_at::date + (scan_num * 3)) as date_scanned,
    1 as scanner_id,  -- FastScanner
    (scan_num * 3 + p.germ_day) as plant_age_days,
    (p.created_at::date + (scan_num * 3) + interval '1 hour') as uploaded_at
FROM cyl_plants p
CROSS JOIN generate_series(1, 6) as scan_num
WHERE p.qr_code LIKE 'SOY-%'
ON CONFLICT (plant_id, date_scanned) DO NOTHING;

-- ============================================================================
-- 6. SCAN TRAITS - Generate realistic trait values with growth patterns
-- ============================================================================
-- Fix sequence before inserting
SELECT setval('cyl_scan_traits_id_seq', (SELECT COALESCE(MAX(id), 0) + 1 FROM cyl_scan_traits), false);

INSERT INTO cyl_scan_traits (scan_id, trait_id, value)
SELECT
    s.id as scan_id,
    t.id as trait_id,
    CASE t.name
        -- Root traits that grow over time
        WHEN 'root_width_max' THEN
            ROUND((20 + s.plant_age_days * 2.5 + (RANDOM() * 15) +
                   CASE WHEN p.qr_code LIKE '%-001' THEN 20 ELSE 0 END)::numeric, 2)  -- Outlier boost for -001 plants
        WHEN 'root_width_mean' THEN
            ROUND((15 + s.plant_age_days * 1.8 + (RANDOM() * 10))::numeric, 2)
        WHEN 'root_depth' THEN
            ROUND((30 + s.plant_age_days * 4.0 + (RANDOM() * 20))::numeric, 2)
        -- Crown angles (relatively stable)
        WHEN 'crown_angle_proximal' THEN
            ROUND((25 + (RANDOM() * 20) +
                   CASE WHEN a.name = 'Williams-82' THEN 10 ELSE 0 END)::numeric, 2)
        WHEN 'crown_angle_distal' THEN
            ROUND((35 + (RANDOM() * 25))::numeric, 2)
        WHEN 'crown_length' THEN
            ROUND((10 + s.plant_age_days * 1.5 + (RANDOM() * 8))::numeric, 2)
        -- Lateral root traits
        WHEN 'lateral_count' THEN
            ROUND((2 + s.plant_age_days * 0.3 + (RANDOM() * 3))::numeric, 0)
        WHEN 'lateral_angle_mean' THEN
            ROUND((45 + (RANDOM() * 30))::numeric, 2)
        -- Primary root
        WHEN 'primary_length' THEN
            ROUND((50 + s.plant_age_days * 5.0 + (RANDOM() * 25) +
                   CASE WHEN a.name = 'PI-416937' THEN 30 ELSE 0 END)::numeric, 2)  -- PI-416937 has longer roots
        -- Shoot traits
        WHEN 'stem_diameter' THEN
            ROUND((2 + s.plant_age_days * 0.15 + (RANDOM() * 1.5))::numeric, 2)
        WHEN 'shoot_height' THEN
            ROUND((20 + s.plant_age_days * 3.0 + (RANDOM() * 15))::numeric, 2)
        WHEN 'leaf_area' THEN
            ROUND((5 + s.plant_age_days * 2.0 + (RANDOM() * 10))::numeric, 2)
        WHEN 'biomass_estimate' THEN
            ROUND((1 + s.plant_age_days * 0.5 + (RANDOM() * 2))::numeric, 2)
        ELSE
            ROUND((50 + (RANDOM() * 50))::numeric, 2)
    END as value
FROM cyl_scans s
JOIN cyl_plants p ON s.plant_id = p.id
JOIN accessions a ON p.accession_id = a.id
CROSS JOIN cyl_traits t
WHERE p.qr_code LIKE 'SOY-%'
  AND NOT EXISTS (
    SELECT 1 FROM cyl_scan_traits st
    WHERE st.scan_id = s.id AND st.trait_id = t.id
  );

-- ============================================================================
-- Summary: Verify what was created
-- ============================================================================
SELECT 'Traits' as table_name, COUNT(*) as count FROM cyl_traits
UNION ALL SELECT 'Accessions', COUNT(*) FROM accessions
UNION ALL SELECT 'Waves', COUNT(*) FROM cyl_waves WHERE experiment_id = 1
UNION ALL SELECT 'Plants (SOY-*)', COUNT(*) FROM cyl_plants WHERE qr_code LIKE 'SOY-%'
UNION ALL SELECT 'Scans', COUNT(*) FROM cyl_scans s JOIN cyl_plants p ON s.plant_id = p.id WHERE p.qr_code LIKE 'SOY-%'
UNION ALL SELECT 'Scan Traits', COUNT(*) FROM cyl_scan_traits
ORDER BY table_name;
