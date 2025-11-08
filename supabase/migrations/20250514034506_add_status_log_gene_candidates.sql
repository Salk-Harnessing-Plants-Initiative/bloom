ALTER TABLE gene_candidates
ADD COLUMN status_logs JSONB DEFAULT '[]';
