-- =============================================================================
-- Remove bloom_user UPDATE on public; SELECT + INSERT retained (issue #341).
--
-- Scope: this is a NO-UPDATE change, not a no-write one. bloom_user keeps SELECT
-- and INSERT (the user_insert_* WITH CHECK(true) policies stay — #341 scopes the
-- cleanup to UPDATE); only UPDATE is removed (DELETE was never granted). storage
-- schema is out of scope: bloom_user keeps its storage.objects UPDATE grant
-- (20260428130000) — a separate concern, deliberately untouched here.
--
-- bloom_user is intended for reads (writes go through superuser-minted bloom_writer
-- accounts). But it still holds a blanket
--   GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public
-- (20260414002000_security_groups.sql) plus five user_update_* RLS policies. Four
-- of those gate on `created_by = auth.uid()` and are inert ONLY because bloom_user
-- lacks USAGE on schema auth — auth.uid() raises "permission denied for schema
-- auth", so the policy never grants a row. The fifth, user_update_accessions, is
-- USING (true) and was the one live UPDATE path. That is security-by-a-withheld-
-- grant: if bloom_user were ever granted auth USAGE for an unrelated reason, those
-- dormant write policies would silently re-activate.
--
-- This migration makes the no-UPDATE design explicit: it removes the UPDATE grant
-- and the dead policies, so bloom_user's actual privileges ARE the design. The one
-- intentional UPDATE path — public.experiment_progress_logs (the gene-page
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
-- postgres-keyed (verified via pg_default_acl: `bloom_user=arw/postgres`; the
-- original 20260414002000 ALTER DEFAULT PRIVILEGES ran under db push as postgres,
-- so it is postgres-keyed in every env that applied it the same way). The default-
-- privilege REVOKE below is issued defensively in BOTH forms — bare (cancels the
-- entry keyed to whoever runs this migration) and explicit FOR ROLE postgres
-- (cancels the postgres-keyed entry regardless of the runner) — so a future table
-- cannot silently re-grant UPDATE even if the runner differs across environments.
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

-- Stop future tables from silently re-granting UPDATE. Issued in both forms
-- defensively: the bare REVOKE cancels the entry keyed to the executing role, and
-- FOR ROLE postgres cancels the postgres-keyed entry created in 20260414002000.
-- Both are no-ops if no matching entry exists, so this is safe and idempotent.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE UPDATE ON TABLES FROM bloom_user;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  REVOKE UPDATE ON TABLES FROM bloom_user;

-- Retain the one intentional UPDATE path.
GRANT UPDATE ON public.experiment_progress_logs TO bloom_user;

-- Drop the now-dead user_update_* policies so the no-UPDATE design is enforced by
-- the privilege set, not by a withheld grant. The experiment_progress_logs UPDATE
-- policy is intentionally NOT dropped.
DROP POLICY IF EXISTS user_update_accessions ON public.accessions;
DROP POLICY IF EXISTS user_update_chat_threads ON public.chat_threads;
DROP POLICY IF EXISTS user_update_cyl_experiments ON public.cyl_experiments;
DROP POLICY IF EXISTS user_update_gene_candidates ON public.gene_candidates;
DROP POLICY IF EXISTS user_update_species ON public.species;

COMMIT;
