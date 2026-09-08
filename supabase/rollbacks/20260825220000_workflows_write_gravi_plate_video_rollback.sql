-- Manual rollback for 20260825220000_workflows_write_gravi_plate_video.sql
--
-- Removes the workflows service's write access: the three graviscan-videos
-- policies, and the helper function it writes the table through.
--
-- *** DROPS A COLUMN. `fps` and everything recorded in it is lost. ***
-- Nothing else in this file destroys data. Comment out the ALTER TABLE below to
-- keep the column; the migration re-adds it with IF NOT EXISTS either way.
--
-- Does NOT revoke anything on storage.objects or storage.buckets. Those grants
-- predate this migration and the cylinder video endpoint runs on them.

BEGIN;

DROP POLICY IF EXISTS workflows_update_graviscan_videos ON storage.objects;
DROP POLICY IF EXISTS workflows_insert_graviscan_videos ON storage.objects;
DROP POLICY IF EXISTS workflows_read_graviscan_videos ON storage.objects;

-- The EXECUTE grant and revoke go with the function.
DROP FUNCTION IF EXISTS public.record_gravi_plate_video(
  bigint, text, int, text, int, int, int, bigint, text
);

ALTER TABLE public.gravi_plate_videos DROP COLUMN IF EXISTS fps;

COMMIT;
