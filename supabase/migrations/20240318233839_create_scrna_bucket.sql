INSERT INTO storage.buckets (id, name)
  VALUES ('scrna', 'scrna')
    ON CONFLICT DO NOTHING;

DROP POLICY IF EXISTS "Authenticated users can select scrna" ON storage.objects;
CREATE POLICY "Authenticated users can select scrna" ON storage.objects
FOR SELECT TO authenticated
USING ( bucket_id = 'scrna' );

DROP POLICY IF EXISTS "Authenticated users can insert scrna" ON storage.objects;
CREATE POLICY "Authenticated users can insert scrna" ON storage.objects
FOR INSERT TO authenticated
WITH CHECK ( bucket_id = 'scrna' );
