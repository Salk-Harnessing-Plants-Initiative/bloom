-- ============================================================================
-- Backfill: scRNA dataset n_cells
-- ============================================================================
-- One-time data cleanup — NOT a schema migration. Sets
-- scrna_datasets.n_cells to the actual COUNT(*) of scrna_cells per
-- dataset_id. The banner pill ("— cells") reads from this column.
--
-- Idempotent: only updates rows where the stored value disagrees with
-- the counted value, so re-running on a synced DB is a no-op.
--
-- Run against PROD:
--   cd /data/bloom/production
--   docker compose -f docker-compose.prod.yml --env-file .env.prod \
--     -p bloom_v2_prod exec -T db-prod \
--     psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
--     < scripts/backfill_scrna_dataset_ncells.sql
--
-- Run against STAGING:
--   cd /data/bloom/staging
--   docker compose -f docker-compose.prod.yml --env-file .env.staging \
--     -p bloom_v2_staging exec -T db-prod \
--     psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
--     < scripts/backfill_scrna_dataset_ncells.sql
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ─── 0) Pre-snapshot ─────────────────────────────────────────────────────────
DO $$
DECLARE
  null_before BIGINT;
  total       BIGINT;
BEGIN
  SELECT COUNT(*) INTO total       FROM public.scrna_datasets;
  SELECT COUNT(*) INTO null_before FROM public.scrna_datasets WHERE n_cells IS NULL;
  RAISE NOTICE 'pre:  scrna_datasets total rows           = %', total;
  RAISE NOTICE 'pre:  scrna_datasets with NULL n_cells    = %', null_before;
END $$;

-- ─── 1) Set n_cells = COUNT(*) of scrna_cells per dataset ────────────────────
-- IS DISTINCT FROM treats NULL ≠ N, so first-time rows are filled and
-- previously-counted rows are only rewritten if the count actually drifted.
WITH counts AS (
  SELECT
    d.id AS dataset_id,
    (SELECT COUNT(*)::int FROM public.scrna_cells c WHERE c.dataset_id = d.id) AS n
  FROM public.scrna_datasets d
),
upd AS (
  UPDATE public.scrna_datasets d
  SET n_cells = counts.n
  FROM counts
  WHERE d.id = counts.dataset_id
    AND d.n_cells IS DISTINCT FROM counts.n
  RETURNING 1
)
SELECT COUNT(*) AS n_updated FROM upd \gset
\echo 'update: scrna_datasets rows updated =' :n_updated

-- ─── 2) Post-snapshot ────────────────────────────────────────────────────────
DO $$
DECLARE
  null_after BIGINT;
  empty_ds   BIGINT;
BEGIN
  SELECT COUNT(*) INTO null_after FROM public.scrna_datasets WHERE n_cells IS NULL;
  SELECT COUNT(*) INTO empty_ds   FROM public.scrna_datasets WHERE n_cells = 0;
  RAISE NOTICE 'post: scrna_datasets with NULL n_cells   = %', null_after;
  RAISE NOTICE 'post: scrna_datasets with n_cells = 0    = %', empty_ds;
END $$;

COMMIT;
