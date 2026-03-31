-- Add anon read policies for scrna tables
-- This allows unauthenticated users to read scrna data

CREATE POLICY "Anon users can select scrna_cells"
ON public.scrna_cells
FOR SELECT
TO anon
USING (true);

CREATE POLICY "Anon users can select scrna_counts"
ON public.scrna_counts
FOR SELECT
TO anon
USING (true);

CREATE POLICY "Anon users can select scrna_genes"
ON public.scrna_genes
FOR SELECT
TO anon
USING (true);

CREATE POLICY "Anon users can select scrna_datasets"
ON public.scrna_datasets
FOR SELECT
TO anon
USING (true);
