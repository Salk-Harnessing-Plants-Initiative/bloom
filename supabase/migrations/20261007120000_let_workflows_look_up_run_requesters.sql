-- 20261007120000_let_workflows_look_up_run_requesters.sql
--
-- The RNA-seq status poller emails the scientist who started a run when it finishes. It signs
-- in as bloom_workflows, which can't read auth.users, so it looks the email up with
-- rnaseq_run_requesters, as the Timeline does. This lets it call that function.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

GRANT EXECUTE ON FUNCTION public.rnaseq_run_requesters(BIGINT[]) TO bloom_workflows;

COMMIT;
