ALTER TABLE assemblies ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can select assemblies" ON assemblies;
CREATE POLICY "Authenticated users can select assemblies"
ON assemblies AS permissive
FOR SELECT TO authenticated
USING (true);
