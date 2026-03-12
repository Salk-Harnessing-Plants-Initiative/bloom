INSERT INTO storage.buckets (id, name)
  VALUES ('species_illustrations', 'species_illustrations')
    ON CONFLICT DO NOTHING;

DROP POLICY IF EXISTS "Authenticated users can select species_illustrations" ON storage.objects;
CREATE POLICY "Authenticated users can select species_illustrations" ON storage.objects
FOR SELECT TO authenticated
USING ( bucket_id = 'species_illustrations' );

DROP POLICY IF EXISTS "Authenticated users can update species_illustrations" ON storage.objects;
CREATE POLICY "Authenticated users can update species_illustrations" ON storage.objects
FOR UPDATE TO authenticated
USING ( bucket_id = 'species_illustrations' );

DROP POLICY IF EXISTS "Authenticated users can delete species_illustrations" ON storage.objects;
CREATE POLICY "Authenticated users can delete species_illustrations" ON storage.objects
FOR DELETE TO authenticated
USING ( bucket_id = 'species_illustrations' );

DROP POLICY IF EXISTS "Authenticated users can insert species_illustrations" ON storage.objects;
CREATE POLICY "Authenticated users can insert species_illustrations" ON storage.objects
FOR INSERT TO authenticated
WITH CHECK ( bucket_id = 'species_illustrations' );
