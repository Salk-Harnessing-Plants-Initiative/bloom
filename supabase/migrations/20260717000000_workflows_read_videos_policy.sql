-- bloom_workflows needs SELECT on the videos bucket: the on-demand video route
-- uploads with upsert (storage-api reads the object back), which RLS blocked when
-- create_workflows_role granted videos write + images read but not videos read.
DROP POLICY IF EXISTS workflows_read_videos_bucket ON storage.objects;
CREATE POLICY workflows_read_videos_bucket ON storage.objects
  FOR SELECT TO bloom_workflows USING (bucket_id = 'videos');
