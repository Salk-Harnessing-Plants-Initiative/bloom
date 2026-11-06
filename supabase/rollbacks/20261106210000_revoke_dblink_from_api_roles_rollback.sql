-- Rollback for 20261106210000_revoke_dblink_from_api_roles.sql
-- Manual break-glass only: this repo applies migrations forward via `supabase db push`
-- (no automated down-runner). Gives PUBLIC back EXECUTE on dblink's functions, which reopens
-- them to every API caller through PostgREST. Safe to run twice.

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
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO PUBLIC', f);
    END LOOP;
END $$;

NOTIFY pgrst, 'reload schema';

COMMIT;
