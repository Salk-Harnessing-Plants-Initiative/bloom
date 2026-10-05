-- Storage bucket for RNA-seq step logs: each step uploads what it prints to
-- run-logs/scrna/<argo workflow name>/<step>.log while it runs, and the run page reads it.
--
-- The pipeline (bloom_workflows) writes; every signed-in user and the agent read; only
-- bloom_admin deletes, so old runs' folders are pruned by hand. bloom_workflows also needs
-- SELECT: an upload with upsert reads the object back (20260717000000).
--
-- bloom_admin, bloom_agent and bloom_writer already reach this bucket through their blanket
-- storage.objects policies (20260506000001, 20260519130000); the explicit admin and agent
-- policies below keep the bucket's access readable in one place. bloom_writer gets none:
-- nothing here needs writers to upload logs.

INSERT INTO storage.buckets (id, name, public)
  VALUES ('run-logs', 'run-logs', false)
    ON CONFLICT (id) DO NOTHING;

-- bloom_admin: full access, including deleting old logs
DROP POLICY IF EXISTS admin_all_run_logs ON storage.objects;
CREATE POLICY admin_all_run_logs ON storage.objects
    FOR ALL TO bloom_admin
    USING (bucket_id = 'run-logs')
    WITH CHECK (bucket_id = 'run-logs');

-- bloom_agent: read-only
DROP POLICY IF EXISTS agent_read_run_logs ON storage.objects;
CREATE POLICY agent_read_run_logs ON storage.objects
    FOR SELECT TO bloom_agent
    USING (bucket_id = 'run-logs');

-- bloom_user: read-only, for the run page
DROP POLICY IF EXISTS user_read_run_logs ON storage.objects;
CREATE POLICY user_read_run_logs ON storage.objects
    FOR SELECT TO bloom_user
    USING (bucket_id = 'run-logs');

-- bloom_workflows: the steps' uploads; read, write and overwrite, no delete
DROP POLICY IF EXISTS workflows_select_run_logs ON storage.objects;
CREATE POLICY workflows_select_run_logs ON storage.objects
    FOR SELECT TO bloom_workflows
    USING (bucket_id = 'run-logs');

DROP POLICY IF EXISTS workflows_insert_run_logs ON storage.objects;
CREATE POLICY workflows_insert_run_logs ON storage.objects
    FOR INSERT TO bloom_workflows
    WITH CHECK (bucket_id = 'run-logs');

DROP POLICY IF EXISTS workflows_update_run_logs ON storage.objects;
CREATE POLICY workflows_update_run_logs ON storage.objects
    FOR UPDATE TO bloom_workflows
    USING (bucket_id = 'run-logs')
    WITH CHECK (bucket_id = 'run-logs');
