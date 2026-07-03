-- Least-privilege DB role for the workflows service.
-- These grants + policies are the entire boundary of what the workflows video
-- endpoint can do:
--   read  : cyl_images, cyl_scans_extended
--   write : the existing `videos` storage bucket (created in 20240313011743 —
--           not re-created here), and cyl_scan_videos — a small record table
--           (scan_id -> storage path) this migration creates. Bloom web plays
--           the file from storage at videos/cyl-videos/{scan_id}.mp4; the table
--           gives a queryable record of which scan has a video and its path.
-- Unlike bloom_agent this is NOT granted on all tables — only what the endpoint
-- touches. Expand explicitly as new endpoints need it. The access method
-- (direct LOGIN vs role-JWT) is set at deploy; these grants apply either way.

-- 1. Role -------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bloom_workflows') THEN
    CREATE ROLE bloom_workflows NOLOGIN;
  END IF;
END
$$;

GRANT bloom_workflows TO authenticator;
GRANT USAGE ON SCHEMA public TO bloom_workflows;

-- 2. Read: cyl_images (RLS on -> grant + policy) ----------------------------
GRANT SELECT ON public.cyl_images TO bloom_workflows;
DROP POLICY IF EXISTS workflows_read_cyl_images ON public.cyl_images;
CREATE POLICY workflows_read_cyl_images ON public.cyl_images
  FOR SELECT TO bloom_workflows USING (true);

-- Read: cyl_scans_extended is a SECURITY DEFINER view, so a grant is enough
-- (it executes as its owner; base-table RLS is not evaluated for this role).
GRANT SELECT ON public.cyl_scans_extended TO bloom_workflows;

-- 3. Storage: read `images` bucket, write the EXISTING `videos` bucket ------
-- Bloom web plays the file from videos/cyl-videos/{scan_id}.mp4
-- (web/components/plant-scan.tsx). The upload also records the object path in
-- storage.objects (Storage API), hence the storage.objects grant.
GRANT USAGE ON SCHEMA storage TO bloom_workflows;
GRANT SELECT ON storage.buckets TO bloom_workflows;
GRANT SELECT, INSERT, UPDATE ON storage.objects TO bloom_workflows;

DROP POLICY IF EXISTS workflows_read_images_bucket ON storage.objects;
CREATE POLICY workflows_read_images_bucket ON storage.objects
  FOR SELECT TO bloom_workflows USING (bucket_id = 'images');

DROP POLICY IF EXISTS workflows_write_videos_insert ON storage.objects;
CREATE POLICY workflows_write_videos_insert ON storage.objects
  FOR INSERT TO bloom_workflows WITH CHECK (bucket_id = 'videos');

DROP POLICY IF EXISTS workflows_write_videos_update ON storage.objects;
CREATE POLICY workflows_write_videos_update ON storage.objects
  FOR UPDATE TO bloom_workflows USING (bucket_id = 'videos') WITH CHECK (bucket_id = 'videos');

-- 4. Record table: cyl_scan_videos -----------------------------------------
-- One row per scan's video (id = video_id, scan_id -> cyl_scans, path in the
-- videos bucket). scan_id is UNIQUE so the endpoint upserts one row per scan.
CREATE TABLE IF NOT EXISTS public.cyl_scan_videos (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_id     bigint NOT NULL UNIQUE REFERENCES public.cyl_scans(id),
  path        text NOT NULL,
  created_at  timestamptz DEFAULT now()
);
ALTER TABLE public.cyl_scan_videos ENABLE ROW LEVEL SECURITY;

-- Column-level write: the endpoint only sets scan_id + path (id/created_at
-- come from defaults). UPDATE(path) supports upsert-on-conflict(scan_id).
GRANT SELECT ON public.cyl_scan_videos TO bloom_workflows;
GRANT INSERT (scan_id, path) ON public.cyl_scan_videos TO bloom_workflows;
GRANT UPDATE (path) ON public.cyl_scan_videos TO bloom_workflows;

DROP POLICY IF EXISTS workflows_insert_cyl_scan_videos ON public.cyl_scan_videos;
CREATE POLICY workflows_insert_cyl_scan_videos ON public.cyl_scan_videos
  FOR INSERT TO bloom_workflows WITH CHECK (true);
DROP POLICY IF EXISTS workflows_update_cyl_scan_videos ON public.cyl_scan_videos;
CREATE POLICY workflows_update_cyl_scan_videos ON public.cyl_scan_videos
  FOR UPDATE TO bloom_workflows USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS workflows_read_cyl_scan_videos ON public.cyl_scan_videos;
CREATE POLICY workflows_read_cyl_scan_videos ON public.cyl_scan_videos
  FOR SELECT TO bloom_workflows USING (true);
