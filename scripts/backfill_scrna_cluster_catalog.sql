-- ============================================================================
-- Backfill: scRNA cluster catalog + stats
-- ============================================================================
-- One-time data cleanup — NOT a schema migration. Lives under scripts/ so
-- the deploy workflow ignores it; run by hand against the target DB via
-- `docker compose exec` (see commands at the bottom of this header).
--
-- Repairs the UMAP "0 clusters / 100% no cluster assignment" state by:
--   1) Splitting scrna_cells.cluster_id values of the form
--      "<cluster>.<...stuff..._repN>" into cluster_id ("<cluster>") and
--      replicate ("repN" only — the bare token, not the full suffix).
--   2) Seeding scrna_clusters from the cleaned distinct cluster_ids
--      (ordinals 0..N-1 per dataset, lex-sorted, name = cluster_id).
--   3) Seeding scrna_cluster_stats (cell_count, pct, centroid_x/y) so
--      the sidebar can show per-cluster counts.
--
-- Idempotent: split step skips rows without a dot; both inserts use
-- ON CONFLICT DO NOTHING. Re-running on a clean DB is a no-op.
--
-- Run against PROD:
--   cd /data/bloom/production
--   docker compose -f docker-compose.prod.yml --env-file .env.prod \
--     -p bloom_v2_prod exec -T db-prod \
--     psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
--     < scripts/backfill_scrna_cluster_catalog.sql
--
-- Run against STAGING:
--   cd /data/bloom/staging
--   docker compose -f docker-compose.prod.yml --env-file .env.staging \
--     -p bloom_v2_staging exec -T db-prod \
--     psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
--     < scripts/backfill_scrna_cluster_catalog.sql
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ─── 0) Snapshot pre-state for the operator log ──────────────────────────────
DO $$
DECLARE
  composite_cells BIGINT;
  catalog_rows    BIGINT;
  stats_rows      BIGINT;
BEGIN
  SELECT COUNT(*) INTO composite_cells
  FROM public.scrna_cells
  WHERE cluster_id IS NOT NULL AND POSITION('.' IN cluster_id) > 0;

  SELECT COUNT(*) INTO catalog_rows FROM public.scrna_clusters;
  SELECT COUNT(*) INTO stats_rows   FROM public.scrna_cluster_stats;

  RAISE NOTICE 'pre: composite cluster_id rows in scrna_cells = %', composite_cells;
  RAISE NOTICE 'pre: scrna_clusters rows                       = %', catalog_rows;
  RAISE NOTICE 'pre: scrna_cluster_stats rows                  = %', stats_rows;
END $$;

-- ─── 1) Seed scrna_clusters FIRST so the FK on scrna_cells is satisfied ─────
-- The FK scrna_cells_cluster_fkey on (dataset_id, cluster_id) is checked
-- against the NEW row value when we update cells in step 2, so the
-- catalog must contain the cleaned cluster_ids before any UPDATE runs.
-- We compute the cleaned values via SPLIT_PART inside the SELECT —
-- scrna_cells is not modified here. Ordinals 0..N-1 per dataset,
-- lex-sorted, name = cluster_id (edit later in Studio for biology).
WITH inserted AS (
  INSERT INTO public.scrna_clusters (dataset_id, cluster_id, ordinal, name)
  SELECT
    dataset_id,
    cluster_id_clean,
    (ROW_NUMBER() OVER (PARTITION BY dataset_id ORDER BY cluster_id_clean) - 1)::smallint,
    cluster_id_clean
  FROM (
    SELECT DISTINCT
      dataset_id,
      SPLIT_PART(cluster_id, '.', 1) AS cluster_id_clean
    FROM public.scrna_cells
    WHERE cluster_id IS NOT NULL
  ) distinct_clusters
  ON CONFLICT (dataset_id, cluster_id) DO NOTHING
  RETURNING 1
)
SELECT COUNT(*) AS cluster_inserts FROM inserted \gset
\echo 'seed: scrna_clusters rows inserted =' :cluster_inserts

-- ─── 2) Split composite cluster_id → (cluster_id, replicate) ─────────────────
-- Source values look like "Cluster1.Gmax_root_no_primary_tip_1wk_2023_rep2".
--   cluster_id ← the part before the first dot ("Cluster1")
--   replicate  ← just the trailing replicate token ("rep2"), captured by
--                the regex `_(rep[0-9]+)$`. If no rep token is present,
--                replicate is set to NULL.
-- Postgres SET expressions evaluate against the OLD row, so both the
-- regex and SPLIT_PART see the un-split cluster_id even though it's
-- also being updated. FK targets were seeded in step 1.
WITH split AS (
  UPDATE public.scrna_cells
  SET
    replicate  = SUBSTRING(cluster_id FROM '_(rep[0-9]+)$'),
    cluster_id = SPLIT_PART(cluster_id, '.', 1)
  WHERE cluster_id IS NOT NULL
    AND POSITION('.' IN cluster_id) > 0
  RETURNING 1
)
SELECT COUNT(*) AS split_count FROM split \gset
\echo 'split: rows updated =' :split_count

-- ─── 3) Seed scrna_cluster_stats (cell_count, pct, centroid_x/y) ─────────────
WITH inserted AS (
  INSERT INTO public.scrna_cluster_stats
    (dataset_id, cluster_id, cell_count, pct, centroid_x, centroid_y)
  SELECT
    c.dataset_id,
    c.cluster_id,
    COUNT(*) AS cell_count,
    (COUNT(*)::real * 100.0
       / NULLIF(SUM(COUNT(*)) OVER (PARTITION BY c.dataset_id), 0))::real AS pct,
    AVG(c.x)::real AS centroid_x,
    AVG(c.y)::real AS centroid_y
  FROM public.scrna_cells c
  WHERE c.cluster_id IS NOT NULL
  GROUP BY c.dataset_id, c.cluster_id
  ON CONFLICT (dataset_id, cluster_id) DO NOTHING
  RETURNING 1
)
SELECT COUNT(*) AS stats_inserts FROM inserted \gset
\echo 'seed: scrna_cluster_stats rows inserted =' :stats_inserts

-- ─── 4) Post-snapshot ────────────────────────────────────────────────────────
DO $$
DECLARE
  composite_cells BIGINT;
  catalog_rows    BIGINT;
  stats_rows      BIGINT;
BEGIN
  SELECT COUNT(*) INTO composite_cells
  FROM public.scrna_cells
  WHERE cluster_id IS NOT NULL AND POSITION('.' IN cluster_id) > 0;

  SELECT COUNT(*) INTO catalog_rows FROM public.scrna_clusters;
  SELECT COUNT(*) INTO stats_rows   FROM public.scrna_cluster_stats;

  RAISE NOTICE 'post: composite cluster_id rows in scrna_cells = %', composite_cells;
  RAISE NOTICE 'post: scrna_clusters rows                       = %', catalog_rows;
  RAISE NOTICE 'post: scrna_cluster_stats rows                  = %', stats_rows;
END $$;

COMMIT;
