-- add gene_candidates.status column

ALTER TABLE gene_candidates
ADD COLUMN status TEXT NOT NULL DEFAULT 'under-investigation'
CHECK (status IN ('under-investigation', 'stopped', 'in-translation', 'translation-confirmed'));

UPDATE gene_candidates SET status = 'under-investigation';
