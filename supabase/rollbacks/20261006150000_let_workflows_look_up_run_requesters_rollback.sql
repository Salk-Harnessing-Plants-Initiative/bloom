-- Rollback for 20261006150000_let_workflows_look_up_run_requesters.sql
-- Manual break-glass only; nothing runs it automatically. Takes back bloom_workflows' call on
-- rnaseq_run_requesters; signed-in scientists, writers and admins keep theirs.

BEGIN;

REVOKE EXECUTE ON FUNCTION public.rnaseq_run_requesters(BIGINT[]) FROM bloom_workflows;

COMMIT;
