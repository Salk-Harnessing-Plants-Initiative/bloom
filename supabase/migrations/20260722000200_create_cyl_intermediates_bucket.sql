-- Storage bucket for cyl_scan_intermediates blob bytes (bloom #407).
--
-- Holds the .slp bytes cyl_scan_intermediates.s3_location points at, uploaded
-- by `bloomctl cyl ingest-result --predictions-dir` before the write-back RPC
-- call. Unlike cyl_scan_intermediates the TABLE (locked to RPC-only writes —
-- see 20260630180000_add_cyl_writeback_rpc.sql), there is no SECURITY DEFINER
-- RPC wrapping Supabase Storage byte writes, so bloom_writer and
-- bloom_workflows need direct storage.objects INSERT/UPDATE here — mirroring
-- the existing bloom_workflows/videos-bucket precedent
-- (20260716000000_create_workflows_role.sql), not the legacy blanket-
-- authenticated images-bucket pattern.
--
-- Note: bloom_admin/bloom_agent/bloom_writer already have blanket,
-- bucket-agnostic storage.objects policies from
-- 20260506000001_bloom_role_rls_policies.sql /
-- 20260519130000_add_bloom_writer_role.sql, so they already cover this
-- bucket with zero new policy needed. Only bloom_workflows (bucket-scoped
-- only, not blanket) and bloom_user (per-bucket SELECT-only, no INSERT/
-- UPDATE anywhere) strictly require new grants. Explicit per-role policies
-- are written for all five roles anyway, matching cyl_scan_intermediates's
-- fully-explicit modern per-role style and keeping this migration
-- self-documenting.

INSERT INTO storage.buckets (id, name, public)
  VALUES ('cyl-intermediates', 'cyl-intermediates', false)
    ON CONFLICT (id) DO NOTHING;

-- bloom_admin: full access
DROP POLICY IF EXISTS admin_all_cyl_intermediates ON storage.objects;
CREATE POLICY admin_all_cyl_intermediates ON storage.objects
    FOR ALL TO bloom_admin
    USING (bucket_id = 'cyl-intermediates')
    WITH CHECK (bucket_id = 'cyl-intermediates');

-- bloom_agent: read-only
DROP POLICY IF EXISTS agent_read_cyl_intermediates ON storage.objects;
CREATE POLICY agent_read_cyl_intermediates ON storage.objects
    FOR SELECT TO bloom_agent
    USING (bucket_id = 'cyl-intermediates');

-- bloom_user: read-only
DROP POLICY IF EXISTS user_read_cyl_intermediates ON storage.objects;
CREATE POLICY user_read_cyl_intermediates ON storage.objects
    FOR SELECT TO bloom_user
    USING (bucket_id = 'cyl-intermediates');

-- bloom_writer: the ingest role (interactive login today) — read + write, no delete.
DROP POLICY IF EXISTS writer_select_cyl_intermediates ON storage.objects;
CREATE POLICY writer_select_cyl_intermediates ON storage.objects
    FOR SELECT TO bloom_writer
    USING (bucket_id = 'cyl-intermediates');

DROP POLICY IF EXISTS writer_insert_cyl_intermediates ON storage.objects;
CREATE POLICY writer_insert_cyl_intermediates ON storage.objects
    FOR INSERT TO bloom_writer
    WITH CHECK (bucket_id = 'cyl-intermediates');

DROP POLICY IF EXISTS writer_update_cyl_intermediates ON storage.objects;
CREATE POLICY writer_update_cyl_intermediates ON storage.objects
    FOR UPDATE TO bloom_writer
    USING (bucket_id = 'cyl-intermediates')
    WITH CHECK (bucket_id = 'cyl-intermediates');

-- bloom_workflows: the eventual pipeline identity — granted ahead of its
-- credential existing (mirrors the #470 EXECUTE-grant precedent). Read +
-- write, no delete. SELECT included from the start (not just write) per the
-- videos-bucket lesson (20260717000000_workflows_read_videos_policy.sql):
-- upload-with-upsert needs to read the object back.
DROP POLICY IF EXISTS workflows_select_cyl_intermediates ON storage.objects;
CREATE POLICY workflows_select_cyl_intermediates ON storage.objects
    FOR SELECT TO bloom_workflows
    USING (bucket_id = 'cyl-intermediates');

DROP POLICY IF EXISTS workflows_insert_cyl_intermediates ON storage.objects;
CREATE POLICY workflows_insert_cyl_intermediates ON storage.objects
    FOR INSERT TO bloom_workflows
    WITH CHECK (bucket_id = 'cyl-intermediates');

DROP POLICY IF EXISTS workflows_update_cyl_intermediates ON storage.objects;
CREATE POLICY workflows_update_cyl_intermediates ON storage.objects
    FOR UPDATE TO bloom_workflows
    USING (bucket_id = 'cyl-intermediates')
    WITH CHECK (bucket_id = 'cyl-intermediates');
