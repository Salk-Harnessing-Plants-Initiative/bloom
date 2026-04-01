-- Create video_jobs queue table for async video generation
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

-- Create index for faster pending job lookups
CREATE INDEX idx_video_jobs_status ON video_jobs(status);
CREATE INDEX idx_video_jobs_scan_id ON video_jobs(scan_id);

-- Enable RLS
ALTER TABLE video_jobs ENABLE ROW LEVEL SECURITY;

-- Allow anon users to read video jobs (to check status)
CREATE POLICY "Anon users can select video_jobs"
ON video_jobs AS PERMISSIVE
FOR SELECT TO anon
USING (true);

-- Allow anon users to insert video jobs (to request video generation)
CREATE POLICY "Anon users can insert video_jobs"
ON video_jobs AS PERMISSIVE
FOR INSERT TO anon
WITH CHECK (true);

-- Allow service role full access (for the listener to update status)
CREATE POLICY "Service role has full access to video_jobs"
ON video_jobs AS PERMISSIVE
FOR ALL TO service_role
USING (true)
WITH CHECK (true);

-- Create function to notify on new video job
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

-- Create trigger to notify on insert
CREATE TRIGGER trigger_video_job_notify
AFTER INSERT ON video_jobs
FOR EACH ROW
EXECUTE FUNCTION notify_video_job();

-- Enable realtime for video_jobs table (for frontend subscriptions)
ALTER PUBLICATION supabase_realtime ADD TABLE video_jobs;

COMMENT ON TABLE video_jobs IS 'Queue table for async video generation jobs';
