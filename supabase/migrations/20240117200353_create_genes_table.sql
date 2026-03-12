-- Creating table 'genes'
CREATE TABLE genes (
    gene_id TEXT PRIMARY KEY,
    reference_id BIGINT REFERENCES assemblies(id),
    name TEXT
);

ALTER TABLE genes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can select genes" ON genes;
CREATE POLICY "Authenticated users can select genes"
ON genes AS permissive
FOR SELECT TO authenticated
USING (true);
