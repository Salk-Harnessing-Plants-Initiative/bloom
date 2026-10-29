-- 20261024120000_enable_realtime_for_species.sql
--
-- Add public.species to the supabase_realtime publication, so the web app's Species page
-- (/app/species) lists a species added or edited elsewhere in Bloom without a reload.
-- Realtime applies species' own row-level security, so subscribers see only the rows
-- they can already read. Signed-in users can't read removed species, so a removal
-- doesn't reach them live: it leaves their list on the next reload.
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
