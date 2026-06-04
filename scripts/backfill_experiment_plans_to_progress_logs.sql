-- ============================================================================
-- Backfill: gene_candidates.experiment_plans_and_progress → experiment_progress_logs
-- ============================================================================
-- One-time data cleanup, NOT a schema migration. Lives under scripts/ so the
-- deploy workflow ignores it; run by hand once per environment.
--
-- The gene_candidates table carries a free-text experiment_plans_and_progress
-- column that holds notes from before the chat-style experiment_progress_logs
-- table existed. The new in-cell preview / modal UI reads from the logs table,
-- so any pre-existing plans-and-progress text is invisible to users today.
-- Copy each non-empty value into experiment_progress_logs as a single log
-- entry, tagged so re-runs and audits can identify backfilled rows.
--
-- The source column is preserved (not cleared) so the original text remains
-- available if anything needs to be reconciled later.
--
-- Idempotent: per row, we INSERT only when a log with the same
-- (gene, message) doesn't already exist. Re-running on a populated DB is a
-- no-op.
--
-- Run against PROD:
--   cd /data/bloom/production
--   docker compose -f docker-compose.prod.yml --env-file .env.prod \
--     -p bloom_v2_prod exec -T db-prod \
--     psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
--     < scripts/backfill_experiment_plans_to_progress_logs.sql
--
-- Run against STAGING:
--   cd /data/bloom/staging
--   docker compose -f docker-compose.prod.yml --env-file .env.staging \
--     -p bloom_v2_staging exec -T db-prod \
--     psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
--     < scripts/backfill_experiment_plans_to_progress_logs.sql
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ─── 0) Pre-snapshot ─────────────────────────────────────────────────────────
DO $$
DECLARE
  total_candidates    BIGINT;
  with_plans_text     BIGINT;
  existing_log_count  BIGINT;
BEGIN
  SELECT COUNT(*) INTO total_candidates FROM public.gene_candidates;
  SELECT COUNT(*) INTO with_plans_text
  FROM public.gene_candidates
  WHERE experiment_plans_and_progress IS NOT NULL
    AND length(btrim(experiment_plans_and_progress)) > 0;
  SELECT COUNT(*) INTO existing_log_count
  FROM public.experiment_progress_logs;

  RAISE NOTICE 'pre:  gene_candidates total                          = %', total_candidates;
  RAISE NOTICE 'pre:  gene_candidates with non-empty plans_text      = %', with_plans_text;
  RAISE NOTICE 'pre:  experiment_progress_logs total                 = %', existing_log_count;
END $$;

-- ─── 1) Copy plans_and_progress → logs (skip duplicates) ────────────────────
-- Each candidate gets at most one backfilled log per unique non-empty value.
-- The NOT EXISTS guard on (gene, message) keeps the script idempotent.
--
-- Timestamp falls back to created_at, then to now() — every row should have a
-- created_at, but the COALESCE is defensive in case any legacy row carries a
-- NULL there.
WITH inserted AS (
  INSERT INTO public.experiment_progress_logs
    (gene, message, timestamp, user_email, tags, links, images)
  SELECT
    gc.gene,
    btrim(gc.experiment_plans_and_progress) AS message,
    COALESCE(gc.created_at, NOW())          AS timestamp,
    NULL                                    AS user_email,
    '[{"label":"backfill-plans-and-progress","color":"#84cc16"}]'::jsonb AS tags,
    '[]'::jsonb                             AS links,
    '[]'::jsonb                             AS images
  FROM public.gene_candidates gc
  WHERE gc.experiment_plans_and_progress IS NOT NULL
    AND length(btrim(gc.experiment_plans_and_progress)) > 0
    AND NOT EXISTS (
      SELECT 1
      FROM public.experiment_progress_logs l
      WHERE l.gene    = gc.gene
        AND l.message = btrim(gc.experiment_plans_and_progress)
    )
  RETURNING 1
)
SELECT COUNT(*) AS n_inserted FROM inserted \gset
\echo 'insert: experiment_progress_logs rows inserted =' :n_inserted

-- ─── 2) Post-snapshot ────────────────────────────────────────────────────────
DO $$
DECLARE
  log_count BIGINT;
BEGIN
  SELECT COUNT(*) INTO log_count FROM public.experiment_progress_logs;
  RAISE NOTICE 'post: experiment_progress_logs total                 = %', log_count;
END $$;

COMMIT;
