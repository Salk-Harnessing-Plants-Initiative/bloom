-- bloom#806: refresh_cyl_experiment_trait_counts() fails with SQLSTATE 21000, "DELETE requires a
-- WHERE clause", once called over the real service_role/PostgREST RPC path (openspec/changes/
-- fix-cyl-scan-traits-latest-rollup/design.md D10).
--
-- Root cause: Postgres's safeupdate extension is loaded via session_preload_libraries on the
-- `authenticator` role alone (Supabase's own vendor-shipped init migration
-- 20220118070449_enable-safeupdate-postgrest.sql, dated 2022 -- not caused by any Postgres version
-- bump this repo made). `authenticator` is the login role PostgREST/Supavisor use before
-- `SET ROLE`-ing to `service_role`; session_preload_libraries loads once at login and survives
-- that role switch, so the guard rejects the deployed 20260817140000 migration's unqualified
-- `DELETE FROM public.cyl_experiment_trait_counts;`. `supabase_admin` (the only role the existing
-- integration test suite connects as) never loads safeupdate, which is why 20+ passing tests never
-- caught this.
--
-- Fix: qualify the DELETE with `WHERE true` -- a no-op change from the database engine's
-- perspective (safeupdate only checks for the syntactic presence of a WHERE clause; a tautological
-- one is not RLS-relevant either, since this function's SECURITY DEFINER owner also owns the
-- table and FORCE ROW LEVEL SECURITY was never set, so RLS never applied to this DELETE in the
-- first place -- confirmed against the live catalog, not assumed). TRUNCATE was considered and
-- rejected (D10): it also satisfies safeupdate, but its AccessExclusiveLock would require
-- re-verifying D5b/D5c's advisory-lock-based concurrency guarantees under different lock
-- semantics, for no benefit over this one-token diff.
--
-- Everything else -- SECURITY DEFINER, the pinned search_path, the advisory lock, the reinsert
-- query -- is copied verbatim from the deployed function; only line 87 of that migration changes.
-- The already-deployed 20260817140000 migration is not edited (migrations are forward-only).
--
-- Manual rollback:
-- supabase/rollbacks/20260910120000_fix_refresh_cyl_experiment_trait_counts_safeupdate_rollback.sql

BEGIN;

CREATE OR REPLACE FUNCTION public.refresh_cyl_experiment_trait_counts()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(0, hashtext('refresh_cyl_experiment_trait_counts'));

    -- bloom#806: qualified with WHERE true so Postgres's safeupdate guard (loaded on the
    -- `authenticator` role and active for every service_role RPC call) does not reject this
    -- statement. See this migration's header comment for the full investigation.
    DELETE FROM public.cyl_experiment_trait_counts WHERE true;
    INSERT INTO public.cyl_experiment_trait_counts (experiment_id, n_traits, updated_at)
    SELECT d.experiment_id, count(*), now()
    FROM (
        SELECT DISTINCT w.experiment_id, cst.trait_id
        FROM public.cyl_waves              w
        JOIN public.cyl_plants             p   ON p.wave_id = w.id AND p.accession_id IS NOT NULL
        JOIN public.cyl_scans              s   ON s.plant_id = p.id
        JOIN public.cyl_scan_traits        cst ON cst.scan_id = s.id
        JOIN public.cyl_scan_latest_source l   ON l.scan_id = cst.scan_id
            AND cst.source_id IS NOT DISTINCT FROM l.max_source_id
        WHERE cst.trait_id IS NOT NULL
    ) d
    GROUP BY d.experiment_id;
END;
$$;

COMMIT;
