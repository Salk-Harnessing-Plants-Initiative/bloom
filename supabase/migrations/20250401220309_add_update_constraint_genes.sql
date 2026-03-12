-- add insert and update rls to genes table.

ALTER TABLE genes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can insert genes" ON genes;
CREATE POLICY "Authenticated users can insert genes"
ON genes AS permissive
FOR INSERT TO authenticated
WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can update genes" ON genes;
CREATE POLICY "Authenticated users can update genes"
ON genes AS permissive
FOR UPDATE TO authenticated
USING (true);