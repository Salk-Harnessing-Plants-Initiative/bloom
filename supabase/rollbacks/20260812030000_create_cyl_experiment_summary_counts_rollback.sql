-- Manual rollback for 20260812030000_create_cyl_experiment_summary_counts.sql
--
-- Drops everything this migration added, in dependency order: trigger, trigger wrapper function,
-- refresh function, rollup backfill procedure, the shared live-join helper, then the table itself.
--
-- REVERSE-ORDER RULE (design.md's Rollback Ordering note): only safe to apply while Phase 2's
-- get_experiment_summary_counts rewrite (M5) has NOT yet merged -- once it has, that migration's
-- rollback must run first. A PL/pgSQL function body's reference to this table/helper is opaque to
-- Postgres's dependency tracker, so this DROP would otherwise succeed silently and only fail at
-- runtime on the RPC's next call.

BEGIN;

DROP TRIGGER IF EXISTS refresh_cyl_experiment_summary_counts_after_write ON public.cyl_scan_traits;
DROP FUNCTION IF EXISTS public.trigger_refresh_cyl_experiment_summary_counts();
DROP FUNCTION IF EXISTS public.refresh_cyl_experiment_summary_counts_for_scan(bigint);
DROP PROCEDURE IF EXISTS public.backfill_cyl_experiment_summary_counts(bigint);
DROP FUNCTION IF EXISTS public.compute_cyl_experiment_summary_counts_live(bigint, bigint, text);
DROP TABLE IF EXISTS public.cyl_experiment_summary_counts;

COMMIT;
