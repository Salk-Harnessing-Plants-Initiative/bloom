-- Rollback for 20261001180000_get_experiment_traits_collapse_limits.sql (bloom#865).
--
-- This is the STAGING HOT-APPLY only. Applying it by hand leaves 20261001180000
-- recorded as applied, so CI, fresh stacks and the next promotion would still apply
-- it. A durable rollback is a new forward migration whose body is this file. After a
-- hand-apply, run
--   supabase migration repair --status reverted 20261001180000
--
-- Restores the default collapse limits on get_experiment_traits. Its batched reads
-- then time out again at production scale (see the migration's header).

BEGIN;

ALTER FUNCTION public.get_experiment_traits(bigint, bigint, text, text, bigint[])
    RESET join_collapse_limit;
ALTER FUNCTION public.get_experiment_traits(bigint, bigint, text, text, bigint[])
    RESET from_collapse_limit;

COMMIT;
