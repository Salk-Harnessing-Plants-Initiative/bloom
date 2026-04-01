-- Add anon SELECT policy for scrna_de table
DROP POLICY IF EXISTS "Anon users can select scrna_de" ON scrna_de;
CREATE POLICY "Anon users can select scrna_de"
ON scrna_de AS permissive
FOR SELECT TO anon
USING (true);
