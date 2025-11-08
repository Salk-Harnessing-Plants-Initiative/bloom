-- add rls to gene_candidates table
ALTER TABLE gene_candidates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can insert gene_candidates" ON gene_candidates;
CREATE POLICY "Authenticated users can insert gene_candidates"
ON gene_candidates AS permissive
FOR INSERT TO authenticated
WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can update gene_candidates" ON gene_candidates;
CREATE POLICY "Authenticated users can update gene_candidates"
ON gene_candidates AS permissive
FOR UPDATE TO authenticated
USING (true);
