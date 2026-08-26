-- Read access to a plate's frames for the workflows service.
--
-- Grants bloom_workflows SELECT on every column of three tables, and read on one
-- storage bucket. Nothing else, and nothing writable:
--
--   public.gravi_scans          which captures a plate has, and in what order
--   public.gravi_images         the object_path of each capture's image
--   public.gravi_scan_sessions  how many captures the run planned
--   graviscan-images bucket     the images themselves
--
-- The write path -- the record wrapper and the graviscan-videos policies -- is
-- a separate migration.

BEGIN;

-- 1. Tables -----------------------------------------------------------------
-- RLS is on for all three, so each needs a policy as well as a grant. With a
-- grant but no policy, SELECT returns no rows instead of failing.

GRANT SELECT ON public.gravi_scans TO bloom_workflows;
GRANT SELECT ON public.gravi_images TO bloom_workflows;
-- total_cycles is the planned capture count. Without it a run that stopped
-- early renders a short video that looks finished.
GRANT SELECT ON public.gravi_scan_sessions TO bloom_workflows;

DROP POLICY IF EXISTS workflows_read_gravi_scans ON public.gravi_scans;
CREATE POLICY workflows_read_gravi_scans ON public.gravi_scans
  FOR SELECT TO bloom_workflows USING (true);

DROP POLICY IF EXISTS workflows_read_gravi_images ON public.gravi_images;
CREATE POLICY workflows_read_gravi_images ON public.gravi_images
  FOR SELECT TO bloom_workflows USING (true);

DROP POLICY IF EXISTS workflows_read_gravi_scan_sessions ON public.gravi_scan_sessions;
CREATE POLICY workflows_read_gravi_scan_sessions ON public.gravi_scan_sessions
  FOR SELECT TO bloom_workflows USING (true);

-- 2. Storage -----------------------------------------------------------------
-- Lets the workflows service read the graviscan-images bucket.

DROP POLICY IF EXISTS workflows_read_graviscan_images ON storage.objects;
CREATE POLICY workflows_read_graviscan_images ON storage.objects
  FOR SELECT TO bloom_workflows USING (bucket_id = 'graviscan-images');

COMMIT;
