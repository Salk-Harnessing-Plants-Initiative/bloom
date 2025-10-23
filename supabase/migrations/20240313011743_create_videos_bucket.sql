INSERT INTO storage.buckets (id, name)
  VALUES ('videos', 'videos')
    ON CONFLICT DO NOTHING;

DROP POLICY IF EXISTS "Authenticated users can select videos" ON storage.objects;
CREATE POLICY "Authenticated users can select videos" ON storage.objects
FOR SELECT TO authenticated
USING ( bucket_id = 'videos' );

DROP POLICY IF EXISTS "Authenticated users can update videos" ON storage.objects;
CREATE POLICY "Authenticated users can update videos" ON storage.objects
FOR UPDATE TO authenticated
USING ( bucket_id = 'videos' );

DROP POLICY IF EXISTS "Authenticated users can delete videos" ON storage.objects;
CREATE POLICY "Authenticated users can delete videos" ON storage.objects
FOR DELETE TO authenticated
USING ( bucket_id = 'videos' );

DROP POLICY IF EXISTS "Authenticated users can insert videos" ON storage.objects;
CREATE POLICY "Authenticated users can insert videos" ON storage.objects
FOR INSERT TO authenticated
WITH CHECK ( bucket_id = 'videos' );
