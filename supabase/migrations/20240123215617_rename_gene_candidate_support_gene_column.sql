-- rename gene_candidate_support.gene to gene_candidate_support.candidate_id
ALTER TABLE gene_candidate_support RENAME COLUMN gene TO candidate_id;