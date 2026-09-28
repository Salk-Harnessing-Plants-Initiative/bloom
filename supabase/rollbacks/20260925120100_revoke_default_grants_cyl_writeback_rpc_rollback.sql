-- Rollback for 20260925120100_revoke_default_grants_cyl_writeback_rpc.sql
-- Manual break-glass only. Apply as postgres.
--
-- Re-grants EXECUTE on insert_cyl_result_envelope to anon and authenticated,
-- restoring the Supabase default grants the migration removed. This RE-OPENS the
-- gap the migration closed and violates cyl-trait-writeback's grant requirement;
-- only use it if a sanctioned caller unexpectedly depended on those grants, and
-- fix that caller instead as soon as possible. The four sanctioned grants are
-- untouched.

BEGIN;

GRANT EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb, text)
    TO anon, authenticated;

COMMIT;
