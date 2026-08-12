-- ============================================================================
-- Verify: cyl_scan_traits.is_latest backfill completeness
-- ============================================================================
-- Read-only diagnostic (no BEGIN/COMMIT -- nothing is written). Run this after
-- `CALL backfill_cyl_scan_traits_is_latest();` (see
-- supabase/migrations/20260812020000_add_backfill_cyl_scan_traits_is_latest_procedure.sql)
-- and BEFORE the view cutover migration (Phase 2, M4) -- the operator runbook gate this
-- backs (fix-cyl-scan-traits-latest-rollup, design.md's Migration Plan / bloom#637).
--
-- Compares the stored column against the same live computation the view currently uses,
-- for every row. Expect mismatch_count = 0 before proceeding to Phase 2.
--
-- Run against STAGING:
--   cd /data/bloom/staging
--   docker compose -f docker-compose.prod.yml --env-file .env.staging \
--     -p bloom_v2_staging exec -T db-prod \
--     psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
--     < scripts/verify_cyl_scan_traits_is_latest_backfill.sql
-- ============================================================================

\set ON_ERROR_STOP on

SELECT count(*) AS mismatch_count
FROM (
    SELECT
        cst.scan_id,
        cst.source_id,
        cst.is_latest AS stored_is_latest,
        (cst.source_id IS NOT DISTINCT FROM
            max(cst.source_id) OVER (PARTITION BY cst.scan_id)) AS live_is_latest
    FROM public.cyl_scan_traits cst
) x
WHERE stored_is_latest IS DISTINCT FROM live_is_latest;

\echo 'mismatch_count above MUST be 0 before running the rollup backfill or opening the Phase 2 PR.'
