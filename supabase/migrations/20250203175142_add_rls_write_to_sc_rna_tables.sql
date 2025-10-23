-- Adding permissions to the sc_rna_datasets, sc_rna_genes, and sc_rna_counts tables
-- to allow authenticated users to insert, update, and delete rows.

ALTER TABLE scrna_datasets ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can insert species" ON scrna_datasets;
CREATE POLICY "Authenticated users can insert scrna_datasets"
ON scrna_datasets AS permissive
FOR INSERT TO authenticated
WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can update scrna_datasets" ON scrna_datasets;
CREATE POLICY "Authenticated users can update scrna_datasets"
ON scrna_datasets AS permissive
FOR UPDATE TO authenticated
USING (true);

-- DROP POLICY IF EXISTS "Authenticated users can delete scrna_datasets" ON scrna_datasets;
-- CREATE POLICY "Authenticated users can delete scrna_datasets"
-- ON scrna_datasets AS permissive
-- FOR DELETE TO authenticated
-- USING (true);

ALTER TABLE scrna_genes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can insert scrna_genes" ON scrna_genes;
CREATE POLICY "Authenticated users can insert scrna_genes"
ON scrna_genes AS permissive
FOR INSERT TO authenticated
WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can update scrna_genes" ON scrna_genes;
CREATE POLICY "Authenticated users can update scrna_genes"
ON scrna_genes AS permissive
FOR UPDATE TO authenticated
USING (true);

-- DROP POLICY IF EXISTS "Authenticated users can delete scrna_genes" ON scrna_genes;
-- CREATE POLICY "Authenticated users can delete scrna_genes"
-- ON scrna_genes AS permissive
-- FOR DELETE TO authenticated
-- USING (true);

ALTER TABLE scrna_counts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can insert scrna_counts" ON scrna_counts;
CREATE POLICY "Authenticated users can insert scrna_counts"
ON scrna_counts AS permissive
FOR INSERT TO authenticated
WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can update scrna_counts" ON scrna_counts;
CREATE POLICY "Authenticated users can update scrna_counts"
ON scrna_counts AS permissive
FOR UPDATE TO authenticated
USING (true);

-- DROP POLICY IF EXISTS "Authenticated users can delete scrna_counts" ON scrna_counts;
-- CREATE POLICY "Authenticated users can delete scrna_counts"
-- ON scrna_counts AS permissive
-- FOR DELETE TO authenticated
-- USING (true);

ALTER TABLE scrna_cells ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can insert scrna_cells" ON scrna_cells;
CREATE POLICY "Authenticated users can insert scrna_cells"
ON scrna_cells AS permissive
FOR INSERT TO authenticated
WITH CHECK (true);

DROP POLICY IF EXISTS "Authenticated users can update scrna_cells" ON scrna_cells;
CREATE POLICY "Authenticated users can update scrna_cells"
ON scrna_cells AS permissive
FOR UPDATE TO authenticated
USING (true);

-- DROP POLICY IF EXISTS "Authenticated users can delete scrna_cells" ON scrna_cells;
-- CREATE POLICY "Authenticated users can delete scrna_cells"
-- ON scrna_cells AS permissive
-- FOR DELETE TO authenticated
-- USING (true);
