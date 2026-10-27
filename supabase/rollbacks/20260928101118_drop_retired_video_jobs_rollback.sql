-- Rollback: drop_retired_video_jobs
--
-- Restores the table, its indexes, its policies, the notify function and the
-- trigger, as 20260126100000_create_video_jobs_queue.sql created them.
--
-- Read the anon policies before running this. They grant SELECT and INSERT to
-- anon with USING (true) / WITH CHECK (true), which is why the drop was worth
-- doing; restore them only if something is genuinely going to consume the
-- queue again.

CREATE TABLE IF NOT EXISTS video_jobs (
  id serial PRIMARY KEY,
  scan_id int NOT NULL,
  status text DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'complete', 'failed')),
  progress int DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
  total_frames int,
  error_message text,
  download_url text,
  created_at timestamp with time zone DEFAULT now(),
  started_at timestamp with time zone,
  completed_at timestamp with time zone
);

CREATE INDEX IF NOT EXISTS idx_video_jobs_status ON video_jobs(status);
CREATE INDEX IF NOT EXISTS idx_video_jobs_scan_id ON video_jobs(scan_id);

ALTER TABLE video_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Anon users can select video_jobs"
ON video_jobs AS PERMISSIVE
FOR SELECT TO anon
USING (true);

CREATE POLICY "Anon users can insert video_jobs"
ON video_jobs AS PERMISSIVE
FOR INSERT TO anon
WITH CHECK (true);

CREATE POLICY "Service role has full access to video_jobs"
ON video_jobs AS PERMISSIVE
FOR ALL TO service_role
USING (true)
WITH CHECK (true);

CREATE OR REPLACE FUNCTION notify_video_job()
RETURNS TRIGGER AS $$
BEGIN
  PERFORM pg_notify('video_jobs', json_build_object(
    'id', NEW.id,
    'scan_id', NEW.scan_id,
    'action', 'new_job'
  )::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_video_job_notify
AFTER INSERT ON video_jobs
FOR EACH ROW
EXECUTE FUNCTION notify_video_job();

ALTER PUBLICATION supabase_realtime ADD TABLE video_jobs;

COMMENT ON TABLE video_jobs IS 'Queue table for async video generation jobs';
