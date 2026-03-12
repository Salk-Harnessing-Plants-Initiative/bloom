-- add insert and update rls to assembly table.

ALTER TABLE assemblies ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can insert assemblies" ON assemblies;
CREATE POLICY "Authenticated users can insert assemblies"
ON assemblies AS permissive
FOR INSERT TO authenticated
WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can update assemblies" ON assemblies;
CREATE POLICY "Authenticated users can update assemblies"
ON assemblies AS permissive
FOR UPDATE TO authenticated
USING (true);