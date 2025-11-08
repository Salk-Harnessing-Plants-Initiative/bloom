ALTER TABLE experiment_progress_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow authenticated users to read logs"
  ON experiment_progress_logs
  FOR SELECT
  TO authenticated
  USING (true);

CREATE POLICY "Allow authenticated users to insert logs"
  ON experiment_progress_logs
  FOR INSERT
  TO authenticated
  WITH CHECK (true);
