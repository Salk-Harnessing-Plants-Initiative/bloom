-- Let get_experiment_traits' scan filter reach cyl_scan_traits (bloom#865).
--
-- Both of the function's reads join nine (recipe branch) or ten (latest/source/run
-- branch) relations once the cyl_scan_traits_source view is expanded, and every join
-- is an explicit JOIN. That is above the default join_collapse_limit (8), so the
-- planner plans the view on its own and the batch's scan ids, which arrive only
-- through a join, never reach cyl_scan_traits: it is read in full and filtered
-- afterwards. On staging (2026-10-01, 28.9M trait rows, bloom_user) a 10-scan recipe
-- read took 14.1 s, over the authenticator role's 8 s statement_timeout; with the
-- join flattened it used idx_cyl_scan_traits and took 50 ms, with the same rows.
-- from_collapse_limit does not apply: no join here is a comma-separated FROM list.
--
-- join_collapse_limit = 11 covers the ten relations and stays below geqo_threshold
-- (12), so planning stays exhaustive and deterministic. A body that joins more than
-- 11 relations needs this revisited.
--
-- plan_cache_mode = force_custom_plan: PostgREST reuses connections, and plpgsql may
-- switch to a generic plan after five calls. In a generic plan the scan_ids_ IS NULL
-- and selector disjunctions cannot be folded away, and on dev it read cyl_scan_traits
-- in full again. Planning ten relations per call is cheap. Same approach as
-- 20260710000200_search_accession_genes_force_custom_plan.
--
-- Settings attached to a function apply while it runs and are restored on exit, so
-- callers' own planner settings are unchanged. The rows returned do not change; the
-- order among rows that tie on the ORDER BY is unspecified, as before.
--
-- CREATE OR REPLACE FUNCTION resets these settings unless it repeats them. Any later
-- migration that replaces get_experiment_traits must carry
--     SET join_collapse_limit = 11
--     SET plan_cache_mode = force_custom_plan
-- (whitespace-separated, alongside its SET search_path);
-- tests/integration/test_get_experiment_traits_plan.py fails otherwise.
--
-- Manual rollback: supabase/rollbacks/20261001180000_get_experiment_traits_collapse_limits_rollback.sql

BEGIN;

ALTER FUNCTION public.get_experiment_traits(bigint, bigint, text, text, bigint[])
    SET join_collapse_limit = 11;
ALTER FUNCTION public.get_experiment_traits(bigint, bigint, text, text, bigint[])
    SET plan_cache_mode = force_custom_plan;

COMMIT;
