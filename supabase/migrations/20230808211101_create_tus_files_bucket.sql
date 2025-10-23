INSERT INTO storage.buckets (id, name)
  VALUES ('tus-files', 'tus-files')
    ON CONFLICT DO NOTHING;

DROP POLICY IF EXISTS "allow uploads" ON storage.objects;
CREATE POLICY "allow uploads" ON storage.objects FOR INSERT TO public WITH CHECK (bucket_id = 'tus-files');

