-- =============================================================================
-- Make bloom_user read-only on public, explicitly (issue #341).
--
-- bloom_user is meant to be read-only (writes go through superuser-minted
-- bloom_writer accounts). But it still holds a blanket
--   GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public
-- (20260414002000_security_groups.sql) plus five user_update_* RLS policies. Four
-- of those gate on `created_by = auth.uid()` and are inert ONLY because bloom_user
-- lacks USAGE on schema auth — auth.uid() raises "permission denied for schema
-- auth", so the policy never grants a row. That is security-by-a-withheld-grant:
-- if bloom_user were ever granted auth USAGE for an unrelated reason, those dormant
-- write policies would silently re-activate.
--
-- This migration makes the read-only design explicit: it removes the UPDATE grant
-- and the dead policies, so bloom_user's actual privileges ARE the design. The one
-- intentional write path — public.experiment_progress_logs (the gene-page
-- "Progress" panel; its policy is FOR UPDATE USING (true), never touching auth) —
-- is retained.
--
-- SUPERSEDES the now-stale header of 20260414002000_security_groups.sql
-- ("bloom_user: SELECT, INSERT, UPDATE ... can only UPDATE own rows"). That file is
-- an applied migration and MUST NOT be edited (it would break `supabase db push`
-- history validation), so the correction lives here + in _WIKI/SUPABASE/README.md.
--
-- Why this applies under `db push` (unlike #333's auth/storage schema grants, which
-- no-op): these objects are postgres-owned and db push executes migration DDL as
-- postgres, which owns them. The default-privileges entry for bloom_user is
-- postgres-keyed (verified via pg_default_acl: `bloom_user=arw/postgres`), so the
-- REVOKE below uses an explicit FOR ROLE postgres to match and cancel it — a bare
-- ALTER DEFAULT PRIVILEGES revoke run as a different superuser would silently no-op.
--
-- Forward-fix rollback (this repo ships no down-migrations):
--   GRANT UPDATE ON ALL TABLES IN SCHEMA public TO bloom_user;
--   ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
--     GRANT UPDATE ON TABLES TO bloom_user;
--   -- plus re-CREATE POLICY for any dropped user_update_* policy found load-bearing
--
-- Idempotent: DROP POLICY IF EXISTS + naturally-idempotent REVOKE/GRANT.
-- =============================================================================

BEGIN;

-- Revoke the blanket UPDATE on every existing public table (covers the
-- security_groups blanket grant and the per-table scrna grants in one statement).
REVOKE UPDATE ON ALL TABLES IN SCHEMA public FROM bloom_user;

-- Stop future tables from silently re-granting UPDATE. FOR ROLE postgres matches
-- the postgres-keyed default-privilege entry created in 20260414002000.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE UPDATE ON TABLES FROM bloom_user;

-- Retain the one intentional write path.
GRANT UPDATE ON public.experiment_progress_logs TO bloom_user;

-- Drop the now-dead user_update_* policies so read-only is enforced by the
-- privilege set, not by a withheld grant. The experiment_progress_logs UPDATE
-- policy is intentionally NOT dropped.
DROP POLICY IF EXISTS user_update_accessions ON public.accessions;
DROP POLICY IF EXISTS user_update_chat_threads ON public.chat_threads;
DROP POLICY IF EXISTS user_update_cyl_experiments ON public.cyl_experiments;
DROP POLICY IF EXISTS user_update_gene_candidates ON public.gene_candidates;
DROP POLICY IF EXISTS user_update_species ON public.species;

COMMIT;
