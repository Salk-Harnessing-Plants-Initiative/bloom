-- add experiment logs columns

ALTER TABLE gene_candidates
ADD COLUMN experiment_progress_logs JSONB DEFAULT '[]';
