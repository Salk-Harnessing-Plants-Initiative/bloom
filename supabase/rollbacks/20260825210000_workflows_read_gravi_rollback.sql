-- Manual rollback for 20260825210000_workflows_read_gravi.sql
--
-- Removes the workflows service's read access to a plate's frames: the five
-- policies that migration created, and the four table grants.
--
-- Does NOT revoke anything on storage.objects or storage.buckets. Those grants
-- predate this migration and the cylinder video endpoint runs on them.

BEGIN;

DROP POLICY IF EXISTS workflows_read_graviscan_images ON storage.objects;

DROP POLICY IF EXISTS workflows_read_gravi_plate_videos ON public.gravi_plate_videos;
DROP POLICY IF EXISTS workflows_read_gravi_scan_sessions ON public.gravi_scan_sessions;
DROP POLICY IF EXISTS workflows_read_gravi_images ON public.gravi_images;
DROP POLICY IF EXISTS workflows_read_gravi_scans ON public.gravi_scans;

REVOKE SELECT ON public.gravi_plate_videos FROM bloom_workflows;
REVOKE SELECT ON public.gravi_scan_sessions FROM bloom_workflows;
REVOKE SELECT ON public.gravi_images FROM bloom_workflows;
REVOKE SELECT ON public.gravi_scans FROM bloom_workflows;

COMMIT;
