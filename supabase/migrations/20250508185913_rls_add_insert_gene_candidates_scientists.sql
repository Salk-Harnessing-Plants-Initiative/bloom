ALTER TABLE gene_candidate_scientists ENABLE ROW LEVEL SECURITY;

-- Allow all authenticated users to insert rows
CREATE POLICY "Allow insert for authenticated users"
ON gene_candidate_scientists
FOR INSERT
TO authenticated
WITH CHECK (true);
