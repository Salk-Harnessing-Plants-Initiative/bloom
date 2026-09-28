-- Restrict EXECUTE on the cyl write-back RPC to its sanctioned roles.
-- Change: repin-cyl-contract-a9 (bloom#895).
--
-- WHY: cyl-trait-writeback already requires that EXECUTE on
--   insert_cyl_result_envelope is granted only to bloom_writer, service_role,
--   bloom_admin and bloom_workflows. Supabase's default privileges also grant
--   EXECUTE on every new public function to anon and authenticated, directly
--   (20260730120000 documents this), and every definition of this RPC revoked
--   only FROM PUBLIC, which does not remove those direct grants. It was the one
--   SECURITY DEFINER function in public still missing the repo's usual
--   REVOKE ... FROM PUBLIC, anon, authenticated.
--
-- WHAT: an ACL-only change. No function body, owner or signature change, so the
--   a9 migration stays the newest definition. The grant to the four sanctioned
--   roles is re-asserted. No legitimate caller uses anon or authenticated
--   (bloomctl calls as bloom_writer, cluster write-back as bloom_workflows).
--   Future re-pins that copy this function's body must carry the full REVOKE.
--   Enforced by test_execute_grants_are_exactly_the_sanctioned_roles and
--   tests/integration/test_security_definer_grants.py.
--
-- Forward-only. Manual rollback (re-opens the default grants):
--   supabase/rollbacks/20260925120100_revoke_default_grants_cyl_writeback_rpc_rollback.sql

BEGIN;

REVOKE EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb, text)
    TO bloom_writer, service_role, bloom_admin, bloom_workflows;

COMMIT;
