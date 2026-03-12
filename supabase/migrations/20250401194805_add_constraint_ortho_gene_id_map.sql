-- Add a unique constraint on ortho_group
ALTER TABLE ortho_gene_id_map
ADD CONSTRAINT ortho_group UNIQUE (ortho_group);
