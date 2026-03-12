DROP POLICY IF EXISTS "Authenticated users can select people" ON people;
CREATE POLICY "Authenticated users can select people"
ON people AS permissive
FOR SELECT TO authenticated
USING (true);
