DROP POLICY IF EXISTS "Authenticated users can select gene_candidate_scientists" ON gene_candidate_scientists;
CREATE POLICY "Authenticated users can select gene_candidate_scientists"
ON gene_candidate_scientists AS permissive
FOR SELECT TO authenticated
USING (true);
