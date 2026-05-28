-- Storage bucket for GraviScan image binaries. The desktop app uploads jpegs
-- here keyed by scan id; rows in public.gravi_images point at the object_path.
--
-- Bucket policies mirror the bloom_role pattern in
-- 20260428130000_storage_grants_for_bloom_roles.sql: admin (full), user (read),
-- writer/user (insert + update of own objects).

-- file_size_limit: 500 MB per object (524288000 bytes = 500 * 1024 * 1024).
-- Effective ceiling is min(this, storage-api's FILE_SIZE_LIMIT env var). Both
-- docker-compose.{dev,prod}.yml are aligned at 500 MB so this is the actual cap.
INSERT INTO storage.buckets (id, name, public, file_size_limit)
  VALUES ('graviscan-images', 'graviscan-images', false, 524288000)
    ON CONFLICT (id) DO UPDATE SET file_size_limit = EXCLUDED.file_size_limit;

-- Admin: full access to bucket objects
DROP POLICY IF EXISTS admin_all_graviscan_images ON storage.objects;
CREATE POLICY admin_all_graviscan_images ON storage.objects
    FOR ALL TO bloom_admin
    USING (bucket_id = 'graviscan-images')
    WITH CHECK (bucket_id = 'graviscan-images');

-- bloom_user (inherited by bloom_writer): read objects
DROP POLICY IF EXISTS user_read_graviscan_images ON storage.objects;
CREATE POLICY user_read_graviscan_images ON storage.objects
    FOR SELECT TO bloom_user
    USING (bucket_id = 'graviscan-images');

-- bloom_user (inherited by bloom_writer): upload new objects
DROP POLICY IF EXISTS user_insert_graviscan_images ON storage.objects;
CREATE POLICY user_insert_graviscan_images ON storage.objects
    FOR INSERT TO bloom_user
    WITH CHECK (bucket_id = 'graviscan-images');

-- bloom_user (inherited by bloom_writer): update existing objects (re-uploads)
DROP POLICY IF EXISTS user_update_graviscan_images ON storage.objects;
CREATE POLICY user_update_graviscan_images ON storage.objects
    FOR UPDATE TO bloom_user
    USING (bucket_id = 'graviscan-images');
