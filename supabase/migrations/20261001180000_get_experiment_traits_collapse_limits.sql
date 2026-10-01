-- Let get_experiment_traits' scan filter reach cyl_scan_traits (bloom#865).
--
-- Both of the function's reads join nine or ten relations once the
-- cyl_scan_traits_source view is expanded. That is above the default
-- from_collapse_limit and join_collapse_limit (8), so the planner plans the view on
-- its own and the batch's scan ids, which arrive only through a join, never reach
-- cyl_scan_traits: it is read in full and filtered afterwards. On staging
-- (2026-10-01, 28.9M trait rows, bloom_user) a 10-scan recipe read took 14.1 s, over
-- the authenticator role's 8 s statement_timeout. With both limits at 12 the same
-- query uses idx_cyl_scan_traits and takes 50 ms, with the same 2,070 rows.
--
-- Settings attached to a function apply while it runs and are restored on exit, so
-- callers' own planner settings are unchanged. Results do not change.
--
-- CREATE OR REPLACE FUNCTION resets these settings unless it repeats them. Any later
-- migration that replaces get_experiment_traits must carry
-- SET join_collapse_limit = 12, SET from_collapse_limit = 12;
-- tests/integration/test_get_experiment_traits_plan.py fails otherwise.

BEGIN;

ALTER FUNCTION public.get_experiment_traits(bigint, bigint, text, text, bigint[])
    SET join_collapse_limit = 12;
ALTER FUNCTION public.get_experiment_traits(bigint, bigint, text, text, bigint[])
    SET from_collapse_limit = 12;

COMMIT;
