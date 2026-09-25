-- 20260924120000_add_cyl_pipeline_run_experiments.sql
--
-- add-cyl-pipeline-ui (bloom#15, design §10), PR 1: a read-only view mapping each pipeline run
-- to every experiment its *requested* scans belong to, plus an index on
-- cyl_pipeline_run_scans(scan_id).
--
-- Why a view: the web UI's planned experiment-page runs panel lists the runs that touched that
-- experiment, and nothing else can answer that. `cyl_pipeline_runs.target_id` has no FK, and
-- `scan_ids` runs have no target at all, so membership has to come from `cyl_pipeline_run_scans`.
-- The path runs through the scans a run requested, never through `cyl_trait_sources` provenance
-- (see the change's design D6). Membership is computed from *current* plant and wave assignments:
-- if a plant's wave_id or a wave's experiment_id is later corrected, that run's rows follow it.
--
-- security_invoker: the caller's own RLS and grants on the six base tables apply, so the view
-- exposes nothing its readers can't already read. The `cyl_experiments` join drops runs of
-- soft-deleted experiments for bloom_user and bloom_agent (their policy is
-- `deleted_at IS NULL`). This is a UI filter, not an access boundary: those roles can still reach
-- the experiment id through the base tables, whose policies are `USING (true)`. bloom_admin and
-- bloom_writer (whose own cyl_experiments policy is `USING (true)`) see those runs.
--
-- Index: scan_id is a foreign key to cyl_scans with no index, so the view's join, FK checks on
-- cyl_scans deletes, and any scan -> runs lookup all seq-scan cyl_pipeline_run_scans without it.
--
-- lock_timeout: CREATE INDEX takes a SHARE lock on cyl_pipeline_run_scans, which blocks the
-- trigger's inserts and the poller's and write-back's updates until COMMIT. The table is small,
-- so the build itself takes milliseconds; the timeout makes a deploy fail fast instead of queueing
-- every writer behind a long-lived transaction. If it fires, this file's transaction rolls back
-- and is not recorded, so re-running the deploy is safe. CONCURRENTLY is impossible here:
-- `supabase db push` runs each file in a transaction. No earlier migration in this repo sets
-- lock_timeout.
--
-- REVOKE first: default privileges grant writes on every new relation, and which ones fire depends
-- on the role running the migration. `supabase db push` runs as postgres, whose defaults on the
-- dev stack give anon/authenticated/service_role arwdDxt, bloom_user ar, bloom_agent r,
-- bloom_writer arw and bloom_admin arwd; a supabase_admin session adds its own defaults for
-- anon/authenticated/service_role. Revoking everything and then granting SELECT makes the result
-- the same on every environment. bloom_writer reads through its membership in bloom_user.
--
-- NOTIFY pgrst: deploy.yml never restarts the `rest` container, so reload PostgREST's schema
-- cache explicitly rather than relying on the image's event trigger (same as 20260916120000).
--
-- Idempotent, so a re-run of `supabase db push` and the integration tests (which re-apply this
-- body over CI's already-migrated schema) both succeed. The view pins the base columns it reads:
-- a later type change on any of them needs a DROP and re-CREATE of the view (with its grants), and
-- CREATE OR REPLACE VIEW can only append columns, not rename, reorder or retype them.
--
-- Manual rollback: supabase/rollbacks/20260924120000_add_cyl_pipeline_run_experiments_rollback.sql.
-- It must run before 20260730120000's rollback, which drops two of the view's base tables.

BEGIN;

SET LOCAL lock_timeout = '5s';

CREATE INDEX IF NOT EXISTS cyl_pipeline_run_scans_scan_id_idx
    ON public.cyl_pipeline_run_scans (scan_id);

CREATE OR REPLACE VIEW public.cyl_pipeline_run_experiments
WITH (security_invoker = on) AS
SELECT DISTINCT
    rs.run_id,
    w.experiment_id,
    r.created_at
FROM public.cyl_pipeline_run_scans rs
JOIN public.cyl_pipeline_runs r ON r.id = rs.run_id
JOIN public.cyl_scans s ON s.id = rs.scan_id
JOIN public.cyl_plants p ON p.id = s.plant_id
JOIN public.cyl_waves w ON w.id = p.wave_id
JOIN public.cyl_experiments e ON e.id = w.experiment_id;

COMMENT ON VIEW public.cyl_pipeline_run_experiments IS
    'One row per (pipeline run, experiment) reachable from the run''s requested scans, by current '
    'plant/wave assignment. created_at is the run''s. security_invoker: callers see only what '
    'their own base-table RLS allows.';

REVOKE ALL ON public.cyl_pipeline_run_experiments
    FROM PUBLIC, anon, authenticated, service_role,
         bloom_user, bloom_writer, bloom_agent, bloom_admin, bloom_workflows;

GRANT SELECT ON public.cyl_pipeline_run_experiments TO bloom_user, bloom_agent, bloom_admin;

COMMIT;

NOTIFY pgrst, 'reload schema';
