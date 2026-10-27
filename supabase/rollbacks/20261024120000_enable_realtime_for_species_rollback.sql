-- Rollback for 20261024120000_enable_realtime_for_species.sql
-- Manual break-glass only: this repo applies migrations forward via `supabase db push`
-- (no automated down-runner). Takes public.species out of the supabase_realtime
-- publication; the Species page then updates only on reload. A no-op if it isn't there.

BEGIN;

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime DROP TABLE public.species;
EXCEPTION WHEN undefined_object THEN
  NULL;
END$$;

COMMIT;
