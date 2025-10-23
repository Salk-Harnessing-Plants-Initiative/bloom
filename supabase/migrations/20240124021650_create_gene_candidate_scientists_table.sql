-- create association table between the gene_candidates and people tables
CREATE TABLE gene_candidate_scientists (
  gene_candidate_id TEXT REFERENCES gene_candidates(gene) NOT NULL,
  scientist_id BIGINT REFERENCES people(id) NOT NULL,
  PRIMARY KEY (gene_candidate_id, scientist_id)
);
