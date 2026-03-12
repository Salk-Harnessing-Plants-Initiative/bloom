-- add gene_candidates.publication_status column
ALTER TABLE gene_candidates ADD COLUMN publication_status boolean default false;
