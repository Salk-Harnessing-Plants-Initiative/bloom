-- Fix two gene-page bugs:
--
--   1. RLS on public.experiment_progress_logs was created in
--      20250505193420_add-rls-expression-progress-logs.sql with policies only
--      against the built-in `authenticated` role. The custom_access_token_hook
--      now assigns role=bloom_user / bloom_admin / bloom_agent on every login,
--      and those Postgres roles do not match `TO authenticated`, so reads and
--      writes from the gene candidates "Progress" panel returned zero rows or
--      were rejected outright. Add admin_all / agent_read / user_read /
--      user_insert / user_update policies mirroring the pattern established
--      in 20260506000001_bloom_role_rls_policies.sql.
--
--   2. public.gene_candidates carries a column named experiment_progress_logs
--      that overlaps with the separate table of the same name. No code reads
--      the column — every reference points at the table — but the overlap
--      confused readers and trips up grep-driven debugging. Rename the column
--      to legacy_progress_logs_jsonb so the overlap is gone without losing
--      whatever historical data the column may hold.
--
-- Each CREATE POLICY is preceded by DROP POLICY IF EXISTS so this migration
-- is safe to re-run; the column rename uses IF EXISTS so it is also
-- idempotent.

BEGIN;

-- ─── public.experiment_progress_logs ─────────────────────────────────────────
DROP POLICY IF EXISTS admin_all_experiment_progress_logs ON public.experiment_progress_logs;
CREATE POLICY admin_all_experiment_progress_logs
  ON public.experiment_progress_logs
  FOR ALL TO bloom_admin
  USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS agent_read_experiment_progress_logs ON public.experiment_progress_logs;
CREATE POLICY agent_read_experiment_progress_logs
  ON public.experiment_progress_logs
  FOR SELECT TO bloom_agent
  USING (true);

DROP POLICY IF EXISTS user_read_experiment_progress_logs ON public.experiment_progress_logs;
CREATE POLICY user_read_experiment_progress_logs
  ON public.experiment_progress_logs
  FOR SELECT TO bloom_user
  USING (true);

DROP POLICY IF EXISTS user_insert_experiment_progress_logs ON public.experiment_progress_logs;
CREATE POLICY user_insert_experiment_progress_logs
  ON public.experiment_progress_logs
  FOR INSERT TO bloom_user
  WITH CHECK (true);

DROP POLICY IF EXISTS user_update_experiment_progress_logs ON public.experiment_progress_logs;
CREATE POLICY user_update_experiment_progress_logs
  ON public.experiment_progress_logs
  FOR UPDATE TO bloom_user
  USING (true);

-- ─── public.gene_candidates: rename overlapping column ───────────────────────
-- Guarded so re-runs are no-ops (RENAME COLUMN has no IF EXISTS form).
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'gene_candidates'
      AND column_name = 'experiment_progress_logs'
  ) THEN
    ALTER TABLE public.gene_candidates
      RENAME COLUMN experiment_progress_logs TO legacy_progress_logs_jsonb;
  END IF;
END $$;

COMMIT;
