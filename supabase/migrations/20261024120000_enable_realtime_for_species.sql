-- 20261024120000_enable_realtime_for_species.sql
--
-- Add public.species to the supabase_realtime publication, so the web app's Species page
-- (/app/species) lists a species added or removed elsewhere in Bloom without a reload.
-- Realtime applies species' own row-level security, so subscribers see only the rows
-- they can already read.
--
-- Idempotent: re-applying is a no-op if the table is already in the publication.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE public.species;
EXCEPTION WHEN duplicate_object THEN
  NULL;
END$$;

COMMIT;
