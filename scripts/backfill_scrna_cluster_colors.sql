-- ============================================================================
-- Backfill: scRNA cluster colors
-- ============================================================================
-- One-time data cleanup — NOT a schema migration. Paints
-- scrna_clusters.color with a 20-color qualitative palette (Tableau 20)
-- keyed by ordinal mod 20. Only touches rows where color IS NULL, so
-- any hand-picked colors are preserved on re-run.
--
-- Picked Tableau 20 because it's colorblind-friendly and the most
-- familiar palette for scientific scatter plots. Swap the VALUES list
-- below if you want a different scheme (viridis bins, d3 schemeCategory10,
-- a domain palette, etc.) — each row is `(ordinal_mod, hex)`.
--
-- Run against PROD:
--   cd /data/bloom/production
--   docker compose -f docker-compose.prod.yml --env-file .env.prod \
--     -p bloom_v2_prod exec -T db-prod \
--     psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
--     < scripts/backfill_scrna_cluster_colors.sql
--
-- Run against STAGING:
--   cd /data/bloom/staging
--   docker compose -f docker-compose.prod.yml --env-file .env.staging \
--     -p bloom_v2_staging exec -T db-prod \
--     psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
--     < scripts/backfill_scrna_cluster_colors.sql
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ─── 0) Pre-snapshot ─────────────────────────────────────────────────────────
DO $$
DECLARE
  null_before BIGINT;
  total_rows  BIGINT;
BEGIN
  SELECT COUNT(*) INTO total_rows  FROM public.scrna_clusters;
  SELECT COUNT(*) INTO null_before FROM public.scrna_clusters WHERE color IS NULL;
  RAISE NOTICE 'pre:  scrna_clusters total rows         = %', total_rows;
  RAISE NOTICE 'pre:  scrna_clusters with NULL color    = %', null_before;
END $$;

-- ─── 1) Assign hex colors via ordinal mod 20 (Tableau 20 palette) ────────────
WITH palette(idx, hex) AS (
  VALUES
    ( 0, '#4e79a7'), ( 1, '#f28e2c'), ( 2, '#e15759'), ( 3, '#76b7b2'),
    ( 4, '#59a14f'), ( 5, '#edc949'), ( 6, '#af7aa1'), ( 7, '#ff9da7'),
    ( 8, '#9c755f'), ( 9, '#bab0ac'), (10, '#86bcb6'), (11, '#b07aa1'),
    (12, '#d4a6c8'), (13, '#fabfd2'), (14, '#ffbe7d'), (15, '#8cd17d'),
    (16, '#b6992d'), (17, '#499894'), (18, '#79706e'), (19, '#d37295')
),
painted AS (
  UPDATE public.scrna_clusters c
  SET color = p.hex
  FROM palette p
  WHERE c.color IS NULL
    AND p.idx = (c.ordinal::int % 20)
  RETURNING 1
)
SELECT COUNT(*) AS n_painted FROM painted \gset
\echo 'paint: scrna_clusters rows painted =' :n_painted

-- ─── 2) Post-snapshot ────────────────────────────────────────────────────────
DO $$
DECLARE
  null_after BIGINT;
BEGIN
  SELECT COUNT(*) INTO null_after FROM public.scrna_clusters WHERE color IS NULL;
  RAISE NOTICE 'post: scrna_clusters with NULL color   = %', null_after;
END $$;

COMMIT;
