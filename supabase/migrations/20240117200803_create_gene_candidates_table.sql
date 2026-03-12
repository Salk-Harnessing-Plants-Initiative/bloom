-- Creating table 'gene_candidates'
CREATE TABLE gene_candidates (
    gene TEXT PRIMARY KEY REFERENCES genes(gene_id),
    scientist_email TEXT NULL,
    evidence_description TEXT,
    disclosed_to_otd BOOLEAN,
    translation_approval_date TIMESTAMP NULL
);

ALTER TABLE gene_candidates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can select gene_candidates" ON gene_candidates;
CREATE POLICY "Authenticated users can select gene_candidates"
ON gene_candidates AS permissive
FOR SELECT TO authenticated
USING (true);
