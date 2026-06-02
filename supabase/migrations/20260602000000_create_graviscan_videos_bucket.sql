-- Storage bucket for per-plate time-lapse MP4s served by the
-- /app/plate-phenotypes/[speciesId]/[experimentId]/[plateId] page.
-- Convention: object path is "{experiment_id}/{plate_id}.mp4".
--
-- Policies mirror the graviscan-images bucket pattern.

-- file_size_limit: 500 MB per object. Effective ceiling is
-- min(this, storage-api's FILE_SIZE_LIMIT env var); both docker-compose.
-- {dev,prod}.yml are aligned at 500 MB.
INSERT INTO storage.buckets (id, name, public, file_size_limit)
  VALUES ('graviscan-videos', 'graviscan-videos', false, 524288000)
    ON CONFLICT (id) DO UPDATE SET file_size_limit = EXCLUDED.file_size_limit;

-- Admin: full access to bucket objects
DROP POLICY IF EXISTS admin_all_graviscan_videos ON storage.objects;
CREATE POLICY admin_all_graviscan_videos ON storage.objects
    FOR ALL TO bloom_admin
    USING (bucket_id = 'graviscan-videos')
    WITH CHECK (bucket_id = 'graviscan-videos');

-- bloom_user (inherited by bloom_writer): read objects
DROP POLICY IF EXISTS user_read_graviscan_videos ON storage.objects;
CREATE POLICY user_read_graviscan_videos ON storage.objects
    FOR SELECT TO bloom_user
    USING (bucket_id = 'graviscan-videos');

-- bloom_user (inherited by bloom_writer): upload new objects
DROP POLICY IF EXISTS user_insert_graviscan_videos ON storage.objects;
CREATE POLICY user_insert_graviscan_videos ON storage.objects
    FOR INSERT TO bloom_user
    WITH CHECK (bucket_id = 'graviscan-videos');

-- bloom_user (inherited by bloom_writer): update existing objects (re-uploads)
DROP POLICY IF EXISTS user_update_graviscan_videos ON storage.objects;
CREATE POLICY user_update_graviscan_videos ON storage.objects
    FOR UPDATE TO bloom_user
    USING (bucket_id = 'graviscan-videos');
