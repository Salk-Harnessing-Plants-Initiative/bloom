-- Add public.cyl_scans to the supabase_realtime publication so the web app's
-- "Recent experiments by cylinder scanner" home-page widget can subscribe to
-- INSERT events on this table and refresh without a page reload.
--
-- Idempotent: re-applying is a no-op if the table is already in the
-- publication.

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE public.cyl_scans;
EXCEPTION WHEN duplicate_object THEN
  NULL;
END$$;
