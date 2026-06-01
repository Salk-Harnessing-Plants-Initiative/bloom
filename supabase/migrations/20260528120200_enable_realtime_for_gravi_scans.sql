-- Add gravi_scans and gravi_images to the supabase_realtime publication so
-- the home-page "Recent phenotypes by plate scanner" widget can refresh
-- when new plate scans (or their images) land in the DB.

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE public.gravi_scans;
EXCEPTION WHEN duplicate_object THEN
  NULL;
END$$;

DO $$
BEGIN
  ALTER PUBLICATION supabase_realtime ADD TABLE public.gravi_images;
EXCEPTION WHEN duplicate_object THEN
  NULL;
END$$;
