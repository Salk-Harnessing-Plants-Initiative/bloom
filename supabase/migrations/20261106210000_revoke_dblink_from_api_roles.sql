-- 20261106210000_revoke_dblink_from_api_roles.sql
--
-- The first-boot script installs dblink into `public`, so PostgREST exposes its functions as
-- /rest/v1/rpc/dblink_* and Postgres's default lets PUBLIC execute them: any API caller, the web's
-- anon key included, could make the database server open connections to other hosts.
-- Nothing calls dblink through the API; only the superuser's first-boot script uses it, and a
-- superuser keeps every privilege. Does nothing where dblink isn't installed.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

DO $$
DECLARE
    f regprocedure;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure
          FROM pg_proc p
          JOIN pg_depend d ON d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
          JOIN pg_extension e ON e.oid = d.refobjid AND e.extname = 'dblink'
    LOOP
        EXECUTE format(
            'REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC, anon, authenticated, service_role, '
            'bloom_user, bloom_writer, bloom_admin, bloom_agent, bloom_workflows',
            f
        );
    END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';

COMMIT;
