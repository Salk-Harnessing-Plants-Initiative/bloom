-- Re-apply the storage-schema USAGE grant that `supabase db push` no-ops on.
--
-- `db push` runs every migration after `SET SESSION ROLE postgres`, and in this
-- self-hosted stack `postgres` is neither the owner of schema `storage`
-- (`supabase_admin`) nor a superuser, so the `GRANT USAGE ON SCHEMA storage`
-- in 20260428130000 silently no-ops ("WARNING: no privileges were granted").
-- `bloom_agent` then cannot resolve `storage.objects` and the bloommcp
-- SupabaseResultStore write path fails with `relation "objects" does not exist`.
-- `make migrate-local` pipes this file as the schema owner (`supabase_admin`),
-- outside the db-push role downgrade, so the grant sticks. The prod/staging
-- migration runner needs the same privileged re-grant (tracked in #333).
--
-- This lives in a .sql file (piped via psql stdin) rather than `psql -c "..."`
-- so the PL/pgSQL `$grant$` dollar-quoting is not mangled by Make/shell
-- `$`-expansion.
--
-- The role set is the canonical union of the migrations — bloom_user/admin/agent
-- get storage USAGE in 20260428130000, bloom_writer in 20260519130000 — so local
-- matches the migrations rather than being a divergent definition. Each role is
-- guarded with `IF EXISTS` so a role a not-yet-applied migration hasn't created
-- is skipped with a NOTICE instead of aborting `migrate-local` under
-- ON_ERROR_STOP=1.
DO $grant$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['bloom_user', 'bloom_admin', 'bloom_agent', 'bloom_writer'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('GRANT USAGE ON SCHEMA storage TO %I', r);
    ELSE
      RAISE NOTICE 'role % absent; skipping storage USAGE grant', r;
    END IF;
  END LOOP;
END
$grant$;
