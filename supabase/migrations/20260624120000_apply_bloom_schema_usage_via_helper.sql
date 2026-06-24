-- Apply the bloom_* schema-USAGE grant SET by CALLING the SECURITY DEFINER helper
-- public.bloom_grant_schema_usage (installed by the privileged init layer; see
-- supabase/grants/install_bloom_grant_helper.sql + supabase/grants/README.md).
--
-- This migration is the single source of truth for the grant SET; the helper is the
-- source of truth for the grant MECHANISM. The grant pairs below mirror
-- supabase/grants/bloom_grant_matrix.json (a CI anti-drift test asserts they match).
--
-- A raw "GRANT USAGE ON SCHEMA storage TO bloom_*" here would silently no-op: db
-- push applies this as role postgres, which cannot grant on the supabase_admin-owned
-- storage/auth schemas. The helper runs the grant with the owner's authority. If the
-- helper is absent (an env that has not installed it), these calls error loudly
-- rather than no-oping. (Issue #333. The auth-schema gap for bloom_user/admin/agent
-- is #341's intentional read-only gap: auth USAGE is granted to bloom_writer only.)

BEGIN;

SELECT public.bloom_grant_schema_usage('storage', 'bloom_user');
SELECT public.bloom_grant_schema_usage('storage', 'bloom_admin');
SELECT public.bloom_grant_schema_usage('storage', 'bloom_agent');
SELECT public.bloom_grant_schema_usage('storage', 'bloom_writer');
SELECT public.bloom_grant_schema_usage('auth', 'bloom_writer');

COMMIT;
