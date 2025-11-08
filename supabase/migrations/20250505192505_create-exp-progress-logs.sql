CREATE TABLE experiment_progress_logs (
    id BIGSERIAL PRIMARY KEY,
    gene TEXT NOT NULL,
    message TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT now(),
    tags JSONB DEFAULT '[]',
    links JSONB DEFAULT '[]',
    images JSONB DEFAULT '[]',
    user_email TEXT
);

ALTER TABLE experiment_progress_logs
ADD CONSTRAINT fk_gene
FOREIGN KEY (gene) REFERENCES gene_candidates(gene)
ON DELETE CASCADE;
